/*
 * touchgrab — サイネージ中にタッチパネルを独占する小さなプログラム（Kindle用）
 *
 * ・タッチパネルの入力を EVIOCGRAB で独占し、Kindle 本来の画面に届かないようにする
 *   （電源ボタンやページめくりボタンなど、タッチ以外の入力は触らない）
 * ・TAPS 回のタップを WINDOW ミリ秒以内に検出したら、終了コード 10 で終わる
 *   （= サイネージ終了の合図）
 * ・SIGTERM 等で終わると、カーネルが独占を自動で解除する（プロセスが死んでも必ず解除される）
 * ・-e を付けると、タップとスワイプを画面の座標で1行ずつ標準出力に書く（タイマー画面の操作用）
 *     tap X Y / swipe left|right|up|down X0 Y0 / touch X Y（長押しなどそれ以外）/ exit（TAPS 回タップで終わる直前）
 *   このときの状態メッセージは標準エラーに出す
 *
 * 使い方: touchgrab [-t タップ回数(0で無効)] [-w ミリ秒] [-e] [-W 画面幅 -H 画面高さ]
 *                   [-s 縦横入れ替え] [-x 左右反転] [-y 上下反転] [-i ファイル]
 *         -i のファイルがある間は TAPS 回タップでも終わらない（タイマーのボタン連打で終わらないように）
 * ビルド: zig cc -target arm-linux-musleabi -Os -static -s -o touchgrab touchgrab.c
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

struct dev {
    int xcode, ycode;
    int xmin, xmax, ymin, ymax;
};

static volatile sig_atomic_t stop_flag = 0;
static void on_sig(int s) { (void)s; stop_flag = 1; }

static long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static int is_touch(int fd, struct dev *dv) {
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
    if (TEST_BIT(ABS_MT_POSITION_X, absbits)) {
        dv->xcode = ABS_MT_POSITION_X; dv->ycode = ABS_MT_POSITION_Y;
    } else if (TEST_BIT(ABS_X, absbits)) {
        dv->xcode = ABS_X; dv->ycode = ABS_Y;
    } else {
        return 0;
    }
    struct input_absinfo ai;
    dv->xmin = 0; dv->xmax = 0; dv->ymin = 0; dv->ymax = 0;
    if (ioctl(fd, EVIOCGABS(dv->xcode), &ai) == 0) { dv->xmin = ai.minimum; dv->xmax = ai.maximum; }
    if (ioctl(fd, EVIOCGABS(dv->ycode), &ai) == 0) { dv->ymin = ai.minimum; dv->ymax = ai.maximum; }
    return 1;
}

static int scale(int v, int lo, int hi, int size, int flip) {
    if (hi <= lo || size <= 0) return v;
    long p = (long)(v - lo) * (size - 1) / (hi - lo);
    if (p < 0) p = 0;
    if (p > size - 1) p = size - 1;
    return flip ? (int)(size - 1 - p) : (int)p;
}

int main(int argc, char **argv) {
    int taps_needed = 3, events = 0, swap = 0, flipx = 0, flipy = 0;
    int scr_w = 1072, scr_h = 1448;
    long window = 2000;
    const char *inhibit = NULL;
    for (int i = 1; i < argc; i++) {
        int more = i < argc - 1;
        if (!strcmp(argv[i], "-t") && more) taps_needed = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-w") && more) window = atol(argv[++i]);
        else if (!strcmp(argv[i], "-W") && more) scr_w = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-H") && more) scr_h = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-i") && more) inhibit = argv[++i];
        else if (!strcmp(argv[i], "-e")) events = 1;
        else if (!strcmp(argv[i], "-s")) swap = 1;
        else if (!strcmp(argv[i], "-x")) flipx = 1;
        else if (!strcmp(argv[i], "-y")) flipy = 1;
    }
    FILE *info = events ? stderr : stdout;

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_sig;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    /* 読み手のシェルが先に終わっても、書き込みで落ちずに poll を続けて SIGTERM を待つ */
    signal(SIGPIPE, SIG_IGN);

    struct pollfd pfd[MAXDEV];
    struct dev devs[MAXDEV];
    int n = 0;
    for (int i = 0; i < 32 && n < MAXDEV; i++) {
        char path[32], name[128] = "?";
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) continue;
        if (is_touch(fd, &devs[n]) && ioctl(fd, EVIOCGRAB, 1) == 0) {
            ioctl(fd, EVIOCGNAME(sizeof name - 1), name);
            fprintf(info, "touchgrab: grabbed %s (%s) x=%d..%d y=%d..%d\n", path, name,
                    devs[n].xmin, devs[n].xmax, devs[n].ymin, devs[n].ymax);
            pfd[n].fd = fd;
            pfd[n].events = POLLIN;
            n++;
        } else {
            close(fd);
        }
    }
    fflush(info);
    if (n == 0) {
        fprintf(stderr, "touchgrab: no touch device found\n");
        return 2;
    }

    int down = 0, explicit_seen = 0, tapcount = 0, slot = 0;
    int rx = -1, ry = -1, sx = 0, sy = 0, pend_down = 0, pend_up = 0;
    long last_ev = 0, first_tap = 0, t_down = 0;
    struct kev ev[64];

    while (!stop_flag) {
        /* 時間切れで起きると CPU が休めず電池を使うので、入力かシグナルが来るまで待ち続ける */
        int r = poll(pfd, n, -1);
        if (r < 0) {
            if (errno == EINTR) continue;
            break;
        }
        for (int d = 0; d < n; d++) {
            if (!(pfd[d].revents & POLLIN)) continue;
            ssize_t len = read(pfd[d].fd, ev, sizeof ev);
            if (len <= 0) continue;
            long t = now_ms();
            struct dev *dv = &devs[d];
            for (int k = 0; k < (int)(len / sizeof(struct kev)); k++) {
                int newdown = 0, tap = 0;
                if (ev[k].type == EV_ABS && ev[k].code == ABS_MT_SLOT) {
                    slot = ev[k].value;
                } else if (ev[k].type == EV_ABS && slot == 0 && ev[k].code == dv->xcode) {
                    rx = ev[k].value;
                } else if (ev[k].type == EV_ABS && slot == 0 && ev[k].code == dv->ycode) {
                    ry = ev[k].value;
                } else if (ev[k].type == EV_KEY && ev[k].code == BTN_TOUCH) {
                    explicit_seen = 1;
                    if (ev[k].value && !down) { down = 1; newdown = 1; }
                    if (!ev[k].value && down) { down = 0; pend_up = 1; }
                } else if (ev[k].type == EV_ABS && ev[k].code == ABS_MT_TRACKING_ID && slot == 0) {
                    explicit_seen = 1;
                    if (ev[k].value >= 0 && !down) { down = 1; newdown = 1; }
                    if (ev[k].value < 0 && down) { down = 0; pend_up = 1; }
                } else if (ev[k].type == EV_SYN && ev[k].code == SYN_REPORT) {
                    if (!explicit_seen) {
                        /* 押した/離したの合図が無い機種: しばらく静かだった後の入力を1タップとみなす */
                        if (t - last_ev > 250) newdown = 1;
                    } else if (events) {
                        /* 座標は同じまとまりの最後（SYN_REPORT）でそろうので、ここで押した位置・離した位置を確定する */
                        if (pend_down) { sx = rx; sy = ry; t_down = t; pend_down = 0; }
                        if (pend_up && rx >= 0) {
                            pend_up = 0;
                            int x0 = sx, y0 = sy, x1 = rx, y1 = ry;
                            if (swap) { int a = x0; x0 = y0; y0 = a; a = x1; x1 = y1; y1 = a; }
                            int fx = swap ? dv->ymin : dv->xmin, gx = swap ? dv->ymax : dv->xmax;
                            int fy = swap ? dv->xmin : dv->ymin, gy = swap ? dv->xmax : dv->ymax;
                            x0 = scale(x0, fx, gx, scr_w, flipx); x1 = scale(x1, fx, gx, scr_w, flipx);
                            y0 = scale(y0, fy, gy, scr_h, flipy); y1 = scale(y1, fy, gy, scr_h, flipy);
                            int dx = x1 - x0, dy = y1 - y0;
                            int adx = dx < 0 ? -dx : dx, ady = dy < 0 ? -dy : dy;
                            int slop = (scr_w < scr_h ? scr_w : scr_h) / 20;
                            long dur = t - t_down;
                            if (adx <= slop && ady <= slop && dur < 800) {
                                printf("tap %d %d\n", x0, y0);
                                tap = 1;
                            } else if (dur < 1500 && adx > scr_w / 8 && adx > ady * 2) {
                                printf("swipe %s %d %d\n", dx < 0 ? "left" : "right", x0, y0);
                            } else if (dur < 1500 && ady > scr_h / 8 && ady > adx * 2) {
                                printf("swipe %s %d %d\n", dy < 0 ? "up" : "down", x0, y0);
                            } else {
                                printf("touch %d %d\n", x0, y0);
                            }
                            fflush(stdout);
                        }
                    }
                }
                if (newdown && events && explicit_seen) pend_down = 1;
                /* -e のときはスワイプの出だしを数えないよう、離したときにタップと分かったものだけ数える */
                int count = events && explicit_seen ? tap : newdown;
                if (count && taps_needed > 0 && !(inhibit && access(inhibit, F_OK) == 0)) {
                    if (tapcount == 0 || t - first_tap > window) { tapcount = 0; first_tap = t; }
                    tapcount++;
                    if (tapcount >= taps_needed) {
                        fprintf(info, "touchgrab: %d taps -> exit\n", tapcount);
                        fflush(info);
                        if (events) { printf("exit\n"); fflush(stdout); }
                        return 10; /* 終了すると独占は自動で解除される */
                    }
                } else if (count && inhibit && access(inhibit, F_OK) == 0) {
                    tapcount = 0;
                }
            }
            last_ev = t;
        }
    }
    return 0;
}
