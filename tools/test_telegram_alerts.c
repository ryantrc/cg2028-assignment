#include "../CG2028_Assignment/Core/Inc/telegram_alerts.h"
#include <assert.h>
#include <string.h>

int main(void)
{
    AlertQueue q;
    AlertQueue_Init(&q);
    assert(AlertQueue_Event(&q, ALERT_FALL, 12300));
    assert(!AlertQueue_Event(&q, ALERT_FALL, 12400));
    assert(AlertQueue_Event(&q, ALERT_LONG_LIE, 43000));
    char message[128];
    assert(Alert_FormatMessage(AlertQueue_Front(&q, 12300), "CG2028-01", message, sizeof(message)));
    assert(strstr(message, "Possible fall detected") && strstr(message, "Uptime: 12 s"));
    char request[800];
    assert(Alert_FormatPost(AlertQueue_Front(&q, 12300), "CG2028-01",
                            "123:ABC", "-456", request, sizeof(request)));
    assert(strstr(request, "POST /bot123:ABC/sendMessage HTTP/1.1\r\n"));
    assert(strstr(request, "chat_id=-456&text=Possible+fall+detected."));
    assert(strstr(request, "Uptime%3A+12+s"));
    assert(!Alert_FormatPost(AlertQueue_Front(&q, 12300), "dev", "x\r\nBad: yes", "1", request, sizeof(request)));
    AlertQueue_Result(&q, false, 12300);
    assert(!AlertQueue_Front(&q, 13299));
    assert(AlertQueue_Front(&q, 13300));
    AlertQueue_Result(&q, true, 13300);
    assert(q.count == 1);
    AlertQueue_ResetEpisode(&q);
    assert(AlertQueue_Event(&q, ALERT_FALL, 50000));
    assert(q.items[(q.head + 1) % 4].episode == 1);
    assert(Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\n\r\n{\"ok\":false}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 401 Unauthorized\r\n\r\n{\"ok\":true}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 302 Found\r\n\r\n{\"ok\":true}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\n\r\n{\"ok\":false,\"result\":{\"ok\":true}}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\n\r\n{\"ok\":true"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n{\"ok\":true}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\n\r\n{\"ok\":true,}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\n\r\n{\"ok\":true}garbage"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{\"ok\":true}"));
    assert(Alert_HttpTelegramOK("HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}"));
    assert(!Alert_HttpTelegramOK("HTTP/1.1 429 Too Many Requests\r\n\r\n{\"ok\":false,\"error_code\":429}"));
    AlertQueue retry;
    AlertQueue_Init(&retry);
    assert(AlertQueue_Event(&retry, ALERT_FALL, 1000));
    uint32_t attempt_time = 100000;
    for (unsigned int i = 0; i < 6; i++)
    {
        const AlertItem *front = AlertQueue_Front(&retry, attempt_time);
        assert(front && front->attempts == i);
        AlertQueue_Result(&retry, false, attempt_time);
        attempt_time += 32000;
    }
    assert(retry.count == 0 && retry.dropped == 1);
    return 0;
}
