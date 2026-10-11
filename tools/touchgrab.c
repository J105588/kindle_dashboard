/*
 * touchgrab — サイネージ中にタッチパネルを独占する小さなプログラム（Kindle用）
 *
 * ・タッチパネルの入力を EVIOCGRAB で独占し、Kindle 本来の画面に届かないようにする
 *   （電源ボタンやページめくりボタンなど、タッチ以外の入力は触らない）
 * ・TAPS 回のタップを WINDOW ミリ秒以内に検出したら、終了コード 10 で終わる
 *   （= サイネージ終了の合図）
 * ・SIGTERM 等で終わると、カーネルが独占を自動で解除する（プロセスが死んでも必ず解除される）
 *
 * 使い方: touchgrab [-t タップ回数(0で無効)] [-w ミリ秒]
 * ビルド: zig cc -target arm-linux-musleabi -Os -static -o touchgrab touchgrab.c
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <linux/input.h>

#define MAXDEV 16
#define BITS_PER_LONG (sizeof(long) * 8)
#define NLONGS(x) (((x) + BITS_PER_LONG - 1) / BITS_PER_LONG)
#define TEST_BIT(bit, arr) ((arr[(bit) / BITS_PER_LONG] >> ((bit) % BITS_PER_LONG)) & 1UL)

/* Kindle(32bit ARM)のカーネルが送ってくる input_event の形（16バイト）。
 * musl の time64 と食い違わないよう自前で定義する */
struct kev {
    uint32_t sec, usec;
    uint16_t type, code;
    int32_t value;
};

static volatile sig_atomic_t stop_flag = 0;
static void on_sig(int s) { (void)s; stop_flag = 1; }

static long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static int is_touch(int fd) {
    unsigned long evbits[NLONGS(EV_MAX + 1)];
    unsigned long absbits[NLONGS(ABS_MAX + 1)];
    unsigned long keybits[NLONGS(KEY_MAX + 1)];
    memset(evbits, 0, sizeof evbits);
    memset(absbits, 0, sizeof absbits);
    memset(keybits, 0, sizeof keybits);
    if (ioctl(fd, EVIOCGBIT(0, sizeof evbits), evbits) < 0) return 0;
    if (!TEST_BIT(EV_ABS, evbits)) return 0;
    if (ioctl(fd, EVIOCGBIT(EV_ABS, sizeof absbits), absbits) < 0) return 0;
    if (TEST_BIT(EV_KEY, evbits)) {
        ioctl(fd, EVIOCGBIT(EV_KEY, sizeof keybits), keybits);
        if (TEST_BIT(KEY_POWER, keybits)) return 0; /* 電源ボタンを含む装置は絶対に掴まない */
    }
    return TEST_BIT(ABS_MT_POSITION_X, absbits) || TEST_BIT(ABS_X, absbits);
}

int main(int argc, char **argv) {
    int taps_needed = 3;
    long window = 2000;
    for (int i = 1; i < argc - 1; i++) {
        if (!strcmp(argv[i], "-t")) taps_needed = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-w")) window = atol(argv[++i]);
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_sig;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    struct pollfd pfd[MAXDEV];
    int n = 0;
    for (int i = 0; i < 32 && n < MAXDEV; i++) {
        char path[32], name[128] = "?";
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) continue;
        if (is_touch(fd) && ioctl(fd, EVIOCGRAB, 1) == 0) {
            ioctl(fd, EVIOCGNAME(sizeof name - 1), name);
            printf("touchgrab: grabbed %s (%s)\n", path, name);
            pfd[n].fd = fd;
            pfd[n].events = POLLIN;
            n++;
        } else {
            close(fd);
        }
    }
    fflush(stdout);
    if (n == 0) {
        fprintf(stderr, "touchgrab: no touch device found\n");
        return 2;
    }

    int down = 0, explicit_seen = 0, tapcount = 0;
    long last_ev = 0, first_tap = 0;
    struct kev ev[64];

    while (!stop_flag) {
        int r = poll(pfd, n, 500);
        if (r < 0) {
            if (errno == EINTR) continue;
            break;
        }
        for (int d = 0; d < n; d++) {
            if (!(pfd[d].revents & POLLIN)) continue;
            ssize_t len = read(pfd[d].fd, ev, sizeof ev);
            if (len <= 0) continue;
            long t = now_ms();
            for (int k = 0; k < (int)(len / sizeof(struct kev)); k++) {
                int newdown = 0;
                if (ev[k].type == EV_KEY && ev[k].code == BTN_TOUCH) {
                    explicit_seen = 1;
                    if (ev[k].value && !down) { down = 1; newdown = 1; }
                    if (!ev[k].value) down = 0;
                } else if (ev[k].type == EV_ABS && ev[k].code == ABS_MT_TRACKING_ID) {
                    explicit_seen = 1;
                    if (ev[k].value >= 0 && !down) { down = 1; newdown = 1; }
                    if (ev[k].value < 0) down = 0;
                } else if (!explicit_seen && ev[k].type == EV_SYN && ev[k].code == SYN_REPORT) {
                    /* 押した/離したの合図が無い機種: しばらく静かだった後の入力を1タップとみなす */
                    if (t - last_ev > 250) newdown = 1;
                }
                if (newdown && taps_needed > 0) {
                    if (tapcount == 0 || t - first_tap > window) { tapcount = 0; first_tap = t; }
                    tapcount++;
                    if (tapcount >= taps_needed) {
                        printf("touchgrab: %d taps -> exit\n", tapcount);
                        fflush(stdout);
                        return 10; /* 終了すると独占は自動で解除される */
                    }
                }
            }
            last_ev = t;
        }
    }
    return 0;
}
