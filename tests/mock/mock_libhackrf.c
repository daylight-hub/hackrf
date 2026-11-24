/*
 * Minimal stand-in for libhackrf used by the test-suite.
 * Build: cc -shared -fPIC -pthread -o libhackrf.so.0 mock_libhackrf.c
 *
 * Behaviour is controlled with environment variables:
 *   MOCK_HACKRF_COUNT   number of attached devices (default 1)
 *   MOCK_HACKRF_OPEN    return code of hackrf_device_list_open (e.g. -1000 = access denied, -6 = busy)
 */
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    void *device;
    uint8_t *buffer;
    int buffer_length;
    int valid_length;
    void *rx_ctx;
    void *tx_ctx;
} hackrf_transfer;

typedef int (*hackrf_sample_block_cb_fn)(hackrf_transfer *transfer);

typedef struct {
    char **serial_numbers;
    void *usb_board_ids;
    int *usb_device_index;
    int devicecount;
    void **usb_devices;
    int usb_devicecount;
} hackrf_device_list_t;

typedef struct {
    uint32_t part_id[2];
    uint32_t serial_no[4];
} read_partid_serialno_t;

static int g_dummy_device = 42;
static hackrf_device_list_t g_list;
static char *g_serials[4] = {"000000000000000057b068dc2d5e1a2f", "0000000000000000aaaa", "b", "c"};
static volatile int g_streaming = 0;
static pthread_t g_thread;
static hackrf_sample_block_cb_fn g_cb;

static int env_int(const char *name, int dflt) {
    const char *v = getenv(name);
    return v ? atoi(v) : dflt;
}

int hackrf_init(void) { return 0; }
int hackrf_exit(void) { return 0; }

hackrf_device_list_t *hackrf_device_list(void) {
    g_list.serial_numbers = g_serials;
    g_list.devicecount = env_int("MOCK_HACKRF_COUNT", 1);
    g_list.usb_devicecount = g_list.devicecount;
    return &g_list;
}

int hackrf_device_list_open(hackrf_device_list_t *list, int idx, void **device) {
    (void)list;
    (void)idx;
    int rc = env_int("MOCK_HACKRF_OPEN", 0);
    if (rc == 0) *device = &g_dummy_device;
    return rc;
}

int hackrf_open(void **device) { return hackrf_device_list_open(NULL, 0, device); }
int hackrf_close(void *device) { (void)device; return 0; }
int hackrf_set_freq(void *d, uint64_t f) { (void)d; (void)f; return 0; }
int hackrf_set_sample_rate(void *d, double r) { (void)d; (void)r; return 0; }
int hackrf_set_baseband_filter_bandwidth(void *d, uint32_t b) { (void)d; (void)b; return 0; }
int hackrf_set_lna_gain(void *d, uint32_t g) { (void)d; (void)g; return 0; }
int hackrf_set_vga_gain(void *d, uint32_t g) { (void)d; (void)g; return 0; }
int hackrf_set_txvga_gain(void *d, uint32_t g) { (void)d; (void)g; return 0; }
int hackrf_set_amp_enable(void *d, uint8_t v) { (void)d; (void)v; return 0; }
int hackrf_set_antenna_enable(void *d, uint8_t v) { (void)d; (void)v; return 0; }

int hackrf_board_partid_serialno_read(void *d, read_partid_serialno_t *out) {
    (void)d;
    memset(out, 0, sizeof(*out));
    out->serial_no[2] = 0x57b068dc;
    out->serial_no[3] = 0x2d5e1a2f;
    return 0;
}

static void *rx_thread(void *arg) {
    (void)arg;
    static uint8_t buf[262144];
    unsigned int seed = 1;
    while (g_streaming) {
        for (size_t i = 0; i < sizeof(buf); i++) {
            seed = seed * 1103515245u + 12345u;
            buf[i] = (uint8_t)((seed >> 16) & 0x0f);
        }
        hackrf_transfer t = {&g_dummy_device, buf, (int)sizeof(buf), (int)sizeof(buf), NULL, NULL};
        if (g_cb(&t) != 0) {
            g_streaming = 0;
            break;
        }
        usleep(2000);
    }
    return NULL;
}

int hackrf_start_rx(void *d, hackrf_sample_block_cb_fn cb, void *ctx) {
    (void)d;
    (void)ctx;
    if (g_streaming) return -6;
    g_cb = cb;
    g_streaming = 1;
    pthread_create(&g_thread, NULL, rx_thread, NULL);
    return 0;
}

int hackrf_stop_rx(void *d) {
    (void)d;
    g_streaming = 0;
    pthread_join(g_thread, NULL);
    return 0;
}

int hackrf_start_tx(void *d, hackrf_sample_block_cb_fn cb, void *ctx) { return hackrf_start_rx(d, cb, ctx); }
int hackrf_stop_tx(void *d) { return hackrf_stop_rx(d); }
int hackrf_init_sweep(void *d, const uint16_t *f, int n, uint32_t b, uint32_t w, uint32_t o, int s) {
    (void)d; (void)f; (void)n; (void)b; (void)w; (void)o; (void)s;
    return 0;
}
int hackrf_start_rx_sweep(void *d, hackrf_sample_block_cb_fn cb, void *ctx) { return hackrf_start_rx(d, cb, ctx); }
