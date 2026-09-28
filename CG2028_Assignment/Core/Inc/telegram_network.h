#ifndef TELEGRAM_NETWORK_H
#define TELEGRAM_NETWORK_H

#include "telegram_alerts.h"

/* Runs only in the low-priority FreeRTOS network task. Diagnostic callback
 * receives fixed status lines and must never receive secrets or HTTP bodies. */
void TelegramNetwork_Run(AlertQueue *alerts, void (*diagnostic)(const char *));

#endif
