/*
 * Bounded, receive-only UHD capture with a first-sample hardware timestamp.
 *
 * This helper exists because UHD's generic file example does not preserve the
 * RX metadata time needed by elrs-v3-fresh-sync-clock.py.  It contains no TX
 * streamer and no transmit API call.  Output belongs under ignored work/.
 *
 * Build on Windows (Developer Command Prompt):
 *   cl /nologo /TC /O2 /I"C:\Program Files\UHD\include" \
 *      tools\uhd-elrs-sync-rx.c /Fe:work\uhd-elrs-sync-rx.exe \
 *      /link /LIBPATH:"C:\Program Files\UHD\lib" uhd.lib
 *
 * Build on Linux:
 *   cc -O2 tools/uhd-elrs-sync-rx.c -o work/uhd-elrs-sync-rx -luhd -lm
 *
 * Usage:
 *   uhd-elrs-sync-rx OUTPUT.cf32le MANIFEST.json SERIAL [DURATION_SECONDS]
 *
 * The benchmark is intentionally fixed to the measured TinyHawk v3 SYNC
 * channel and source PHY sample rate. Duration defaults to 2 seconds and is
 * limited to 0.05..5 seconds. Existing output paths are never overwritten.
 */

#include <uhd.h>

#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    float i;
    float q;
} fc32_t;

#define RX_RATE 4062500.0
#define RX_FREQUENCY 2441378800.0
#define RX_GAIN 35.0
#define RX_BANDWIDTH 2000000.0

#define CHECK(call) do {                                                   \
    uhd_error check_error = (call);                                        \
    if (check_error != UHD_ERROR_NONE) {                                   \
        fprintf(stderr, "UHD receive error %d at line %d\n",              \
                (int)check_error, __LINE__);                               \
        return 1;                                                          \
    }                                                                      \
} while (0)

static bool path_exists(const char *path)
{
    FILE *stream = fopen(path, "rb");
    if (stream == NULL) {
        return false;
    }
    fclose(stream);
    return true;
}

int main(int argc, char **argv)
{
    if (argc != 4 && argc != 5) {
        fprintf(stderr, "usage: uhd-elrs-sync-rx OUTPUT.cf32le "
                        "MANIFEST.json SERIAL [DURATION_SECONDS]\n");
        return 2;
    }
    char *end = NULL;
    errno = 0;
    const double duration = argc == 5 ? strtod(argv[4], &end) : 2.0;
    if (errno != 0 || (argc == 5 && (end == argv[4] || *end != '\0'))
            || !isfinite(duration) || duration < 0.05 || duration > 5.0) {
        fprintf(stderr, "duration must be a finite value in 0.05..5 seconds\n");
        return 2;
    }
    if (path_exists(argv[1]) || path_exists(argv[2])) {
        fprintf(stderr, "refusing to overwrite an existing output path\n");
        return 2;
    }
    const size_t requested_samples = (size_t)llround(RX_RATE * duration);
    if (requested_samples == 0) {
        return 2;
    }

    char device_args[96];
    const int device_args_length =
        snprintf(device_args, sizeof(device_args), "serial=%s", argv[3]);
    if (device_args_length < 0
            || (size_t)device_args_length >= sizeof(device_args)) {
        fprintf(stderr, "invalid device serial\n");
        return 2;
    }

    uhd_usrp_handle usrp = NULL;
    uhd_rx_streamer_handle streamer = NULL;
    uhd_rx_metadata_handle metadata = NULL;
    CHECK(uhd_usrp_make(&usrp, device_args));
    CHECK(uhd_rx_streamer_make(&streamer));
    CHECK(uhd_rx_metadata_make(&metadata));
    CHECK(uhd_usrp_set_rx_rate(usrp, RX_RATE, 0));
    char empty[] = "";
    uhd_tune_request_t tune = {
        RX_FREQUENCY, UHD_TUNE_REQUEST_POLICY_AUTO, 0.0,
        UHD_TUNE_REQUEST_POLICY_AUTO, 0.0, empty
    };
    uhd_tune_result_t tune_result;
    CHECK(uhd_usrp_set_rx_freq(usrp, &tune, 0, &tune_result));
    CHECK(uhd_usrp_set_rx_gain(usrp, RX_GAIN, 0, empty));
    CHECK(uhd_usrp_set_rx_bandwidth(usrp, RX_BANDWIDTH, 0));
    CHECK(uhd_usrp_set_rx_antenna(usrp, "RX2", 0));

    size_t channel = 0;
    char cpu[] = "fc32";
    char otw[] = "sc16";
    uhd_stream_args_t stream_args = {cpu, otw, empty, &channel, 1};
    CHECK(uhd_usrp_get_rx_stream(usrp, &stream_args, streamer));
    size_t max_samples = 0;
    CHECK(uhd_rx_streamer_max_num_samps(streamer, &max_samples));
    fc32_t *buffer = (fc32_t *)malloc(max_samples * sizeof(fc32_t));
    if (buffer == NULL) {
        fprintf(stderr, "failed to allocate receive buffer\n");
        return 3;
    }
    FILE *capture = fopen(argv[1], "wb");
    if (capture == NULL) {
        fprintf(stderr, "failed to create capture output\n");
        return 3;
    }

    uhd_stream_cmd_t command = {
        UHD_STREAM_MODE_NUM_SAMPS_AND_DONE,
        requested_samples,
        true,
        0,
        0.0
    };
    CHECK(uhd_rx_streamer_issue_stream_cmd(streamer, &command));
    size_t total = 0;
    bool have_first_time = false;
    int64_t first_full_seconds = 0;
    double first_fractional_seconds = 0.0;
    while (total < requested_samples) {
        size_t amount = requested_samples - total;
        if (amount > max_samples) {
            amount = max_samples;
        }
        void *buffers[1] = {buffer};
        size_t received = 0;
        CHECK(uhd_rx_streamer_recv(streamer, buffers, amount, &metadata,
                                   2.0, false, &received));
        uhd_rx_metadata_error_code_t code;
        CHECK(uhd_rx_metadata_error_code(metadata, &code));
        if (code != UHD_RX_METADATA_ERROR_CODE_NONE) {
            fprintf(stderr, "RX metadata error %d; capture is not clock-safe\n",
                    (int)code);
            return 4;
        }
        if (received == 0) {
            fprintf(stderr, "RX returned zero samples\n");
            return 4;
        }
        if (!have_first_time) {
            bool has_time = false;
            CHECK(uhd_rx_metadata_has_time_spec(metadata, &has_time));
            if (!has_time) {
                fprintf(stderr, "first RX buffer has no UHD timestamp\n");
                return 4;
            }
            CHECK(uhd_rx_metadata_time_spec(metadata, &first_full_seconds,
                                            &first_fractional_seconds));
            have_first_time = true;
        }
        if (fwrite(buffer, sizeof(fc32_t), received, capture) != received) {
            fprintf(stderr, "failed while writing capture\n");
            return 5;
        }
        total += received;
    }
    if (fclose(capture) != 0) {
        fprintf(stderr, "failed to close capture\n");
        return 5;
    }

    double actual_rate = 0.0;
    double actual_frequency = 0.0;
    double actual_gain = 0.0;
    double actual_bandwidth = 0.0;
    CHECK(uhd_usrp_get_rx_rate(usrp, 0, &actual_rate));
    CHECK(uhd_usrp_get_rx_freq(usrp, 0, &actual_frequency));
    CHECK(uhd_usrp_get_rx_gain(usrp, 0, empty, &actual_gain));
    CHECK(uhd_usrp_get_rx_bandwidth(usrp, 0, &actual_bandwidth));

    FILE *manifest = fopen(argv[2], "w");
    if (manifest == NULL) {
        fprintf(stderr, "failed to create manifest output\n");
        return 5;
    }
    fprintf(manifest,
            "{\n"
            "  \"schema\": \"uhd-elrs-receive-anchor-v1\",\n"
            "  \"receive_only\": true,\n"
            "  \"transmit_capable\": false,\n"
            "  \"sample_format\": \"complex-float32-little-endian\",\n"
            "  \"sample_count\": %zu,\n"
            "  \"sample_rate_hz\": %.9f,\n"
            "  \"center_frequency_hz\": %.3f,\n"
            "  \"gain_db\": %.3f,\n"
            "  \"bandwidth_hz\": %.3f,\n"
            "  \"antenna\": \"RX2\",\n"
            "  \"first_sample_uhd_full_seconds\": %lld,\n"
            "  \"first_sample_uhd_fractional_seconds\": %.15f,\n"
            "  \"first_sample_uhd_seconds\": %.15f\n"
            "}\n",
            total, actual_rate, actual_frequency, actual_gain,
            actual_bandwidth, (long long)first_full_seconds,
            first_fractional_seconds,
            (double)first_full_seconds + first_fractional_seconds);
    if (fclose(manifest) != 0) {
        return 5;
    }
    fprintf(stderr, "receive-only capture complete: %zu timestamped samples\n",
            total);

    uhd_rx_metadata_free(&metadata);
    uhd_rx_streamer_free(&streamer);
    uhd_usrp_free(&usrp);
    free(buffer);
    return 0;
}
