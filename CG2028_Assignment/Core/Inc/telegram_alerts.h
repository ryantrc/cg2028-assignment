#ifndef TELEGRAM_ALERTS_H
#define TELEGRAM_ALERTS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { ALERT_FALL, ALERT_LONG_LIE } AlertKind;
typedef struct { AlertKind kind; uint32_t uptime_ms; unsigned int episode; unsigned int attempts; uint32_t next_attempt_ms; } AlertItem;
typedef struct {
    AlertItem items[4];
    unsigned int head, count, episode;
    bool fall_seen, long_lie_seen;
    unsigned int dropped;
} AlertQueue;

void AlertQueue_Init(AlertQueue *queue);
void AlertQueue_ResetEpisode(AlertQueue *queue);
bool AlertQueue_Event(AlertQueue *queue, AlertKind kind, uint32_t uptime_ms);
const AlertItem *AlertQueue_Front(const AlertQueue *queue, uint32_t now_ms);
void AlertQueue_Result(AlertQueue *queue, bool delivered, uint32_t now_ms);
bool Alert_FormatMessage(const AlertItem *item, const char *device_id, char *out, size_t size);
bool Alert_FormatPost(const AlertItem *item, const char *device_id,
                      const char *token, const char *chat_id,
                      char *out, size_t size);
bool Alert_HttpTelegramOK(const char *response);

#endif
