/* Independent expected-output generator: decodes Remote ID payloads with the pinned
 * opendroneid-core-c reference decoder (not WaveRaider's). Input lines:
 *   <frame> <mac> <counter> <hex payload after OUI|app code|counter>
 * Output: one JSON object per line with the reference decoder's valid fields. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "opendroneid.h"

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void json_str(const char *s, size_t max) {
    putchar('"');
    for (size_t i = 0; i < max && s[i]; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 0x20 || c > 0x7e) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: harness payloads.txt\n"); return 2; }
    FILE *f = fopen(argv[1], "r");
    if (!f) { perror("open"); return 1; }
    char line[8192];
    while (fgets(line, sizeof line, f)) {
        int frame, counter;
        char mac[64], hex[4096];
        if (sscanf(line, "%d %63s %d %4095s", &frame, mac, &counter, hex) != 4) continue;
        /* zero-filled buffer large enough for a full 9-message pack */
        uint8_t buf[3 + ODID_MESSAGE_SIZE * ODID_PACK_MAX_MESSAGES + 64];
        memset(buf, 0, sizeof buf);
        size_t n = strlen(hex) / 2;
        if (n > sizeof buf) n = sizeof buf;
        for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)(hexval(hex[2 * i]) << 4 | hexval(hex[2 * i + 1]));

        ODID_UAS_Data u;
        odid_initUasData(&u);
        ODID_messagetype_t t = decodeOpenDroneID(&u, buf);
        printf("{\"frame\":%d,\"mac\":\"%s\",\"counter\":%d,\"bytes\":%zu,\"pack_version\":%d,\"type\":%d",
               frame, mac, counter, n, buf[0] & 0x0F, (int)t);
        for (int i = 0; i < ODID_BASIC_ID_MAX_MESSAGES; i++) {
            if (!u.BasicIDValid[i]) continue;
            printf(",\"basic_id_%d\":{\"id_type\":%d,\"ua_type\":%d,\"uas_id\":", i, u.BasicID[i].IDType, u.BasicID[i].UAType);
            json_str(u.BasicID[i].UASID, ODID_ID_SIZE);
            putchar('}');
        }
        if (u.LocationValid) {
            ODID_Location_data *L = &u.Location;
            printf(",\"location\":{\"status\":%d,\"direction\":%.2f,\"speed_h\":%.2f,\"speed_v\":%.2f,"
                   "\"lat\":%.7f,\"lon\":%.7f,\"alt_baro\":%.1f,\"alt_geo\":%.1f,\"height_type\":%d,\"height\":%.1f,"
                   "\"h_acc\":%d,\"v_acc\":%d,\"baro_acc\":%d,\"speed_acc\":%d,\"ts_acc\":%d,\"timestamp\":%.1f}",
                   L->Status, L->Direction, L->SpeedHorizontal, L->SpeedVertical, L->Latitude, L->Longitude,
                   L->AltitudeBaro, L->AltitudeGeo, L->HeightType, L->Height, L->HorizAccuracy, L->VertAccuracy,
                   L->BaroAccuracy, L->SpeedAccuracy, L->TSAccuracy, L->TimeStamp);
        }
        if (u.SelfIDValid) {
            printf(",\"self_id\":{\"desc_type\":%d,\"desc\":", u.SelfID.DescType);
            json_str(u.SelfID.Desc, ODID_STR_SIZE);
            putchar('}');
        }
        if (u.SystemValid) {
            ODID_System_data *S = &u.System;
            printf(",\"system\":{\"op_location_type\":%d,\"classification\":%d,\"op_lat\":%.7f,\"op_lon\":%.7f,"
                   "\"area_count\":%u,\"area_radius\":%u,\"area_ceiling\":%.1f,\"area_floor\":%.1f,"
                   "\"category_eu\":%d,\"class_eu\":%d,\"op_alt_geo\":%.1f,\"timestamp\":%u}",
                   S->OperatorLocationType, S->ClassificationType, S->OperatorLatitude, S->OperatorLongitude,
                   S->AreaCount, S->AreaRadius, S->AreaCeiling, S->AreaFloor, S->CategoryEU, S->ClassEU,
                   S->OperatorAltitudeGeo, S->Timestamp);
        }
        if (u.OperatorIDValid) {
            printf(",\"operator_id\":{\"type\":%d,\"id\":", u.OperatorID.OperatorIdType);
            json_str(u.OperatorID.OperatorId, ODID_ID_SIZE);
            putchar('}');
        }
        printf("}\n");
    }
    fclose(f);
    return 0;
}
