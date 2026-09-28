#include "telegram_alerts.h"
#include <stdio.h>
#include <string.h>
#include <ctype.h>

void AlertQueue_Init(AlertQueue *q) { memset(q, 0, sizeof(*q)); }

void AlertQueue_ResetEpisode(AlertQueue *q)
{
    q->episode++;
    q->fall_seen = false;
    q->long_lie_seen = false;
}

bool AlertQueue_Event(AlertQueue *q, AlertKind kind, uint32_t now)
{
    bool *seen = kind == ALERT_FALL ? &q->fall_seen : &q->long_lie_seen;
    if (*seen) return false;
    *seen = true;
    if (q->count == 4) { q->dropped++; return false; }
    AlertItem *item = &q->items[(q->head + q->count) % 4];
    *item = (AlertItem){.kind = kind, .uptime_ms = now, .episode = q->episode, .next_attempt_ms = now};
    q->count++;
    return true;
}

const AlertItem *AlertQueue_Front(const AlertQueue *q, uint32_t now)
{
    if (!q->count) return NULL;
    const AlertItem *item = &q->items[q->head];
    return (int32_t)(now - item->next_attempt_ms) >= 0 ? item : NULL;
}

void AlertQueue_Result(AlertQueue *q, bool delivered, uint32_t now)
{
    if (!q->count) return;
    if (delivered || q->items[q->head].attempts >= 5) {
        if (!delivered) q->dropped++;
        q->head = (q->head + 1) % 4;
        q->count--;
    } else {
        AlertItem *item = &q->items[q->head];
        item->next_attempt_ms = now + (1000U << item->attempts);
        item->attempts++;
    }
}

bool Alert_FormatMessage(const AlertItem *item, const char *device, char *out, size_t size)
{
    if (!item || !device || !*device || !out || !size) return false;
    const char *description = item->kind == ALERT_FALL ? "Possible fall detected" :
        "Continued stillness after possible fall";
    int n = snprintf(out, size, "%s. Device: %s. Uptime: %lu s. Episode: %u.",
        description, device, (unsigned long)(item->uptime_ms / 1000U), item->episode);
    return n > 0 && (size_t)n < size;
}

static bool form_encode(const char *source, char *out, size_t size)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t used = 0;
    if (!source || !out || !size) return false;
    for (const unsigned char *p = (const unsigned char *)source; *p; p++)
    {
        unsigned char ch = *p;
        bool plain = (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                     (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' ||
                     ch == '.' || ch == '~';
        size_t added = plain || ch == ' ' ? 1U : 3U;
        if (used + added >= size) return false;
        if (plain) out[used++] = (char)ch;
        else if (ch == ' ') out[used++] = '+';
        else { out[used++] = '%'; out[used++] = hex[ch >> 4]; out[used++] = hex[ch & 15]; }
    }
    out[used] = 0;
    return true;
}

bool Alert_FormatPost(const AlertItem *item, const char *device,
                      const char *token, const char *chat_id, char *out, size_t size)
{
    if (!token || !*token || !chat_id || !*chat_id || !out) return false;
    for (const char *p = token; *p; p++)
        if (!isalnum((unsigned char)*p) && *p != ':' && *p != '_' && *p != '-') return false;
    for (const char *p = chat_id; *p; p++)
        if (!isdigit((unsigned char)*p) && *p != '-') return false;
    char message[160], encoded_message[480], encoded_chat[64], body[560];
    if (!Alert_FormatMessage(item, device, message, sizeof(message)) ||
        !form_encode(message, encoded_message, sizeof(encoded_message)) ||
        !form_encode(chat_id, encoded_chat, sizeof(encoded_chat))) return false;
    int body_len = snprintf(body, sizeof(body), "chat_id=%s&text=%s", encoded_chat, encoded_message);
    if (body_len <= 0 || (size_t)body_len >= sizeof(body)) return false;
    int n = snprintf(out, size,
        "POST /bot%s/sendMessage HTTP/1.1\r\n"
        "Host: api.telegram.org\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n"
        "Content-Length: %d\r\n\r\n%s",
        token, body_len, body);
    return n > 0 && (size_t)n < size;
}

static void JsonSpace(const char **p)
{
    while (isspace((unsigned char)**p)) ++*p;
}

static bool JsonString(const char **p)
{
    if (*(*p)++ != '"') return false;
    while (**p && **p != '"')
    {
        unsigned char ch = (unsigned char)*(*p)++;
        if (ch < 0x20) return false;
        if (ch == '\\')
        {
            if (!**p) return false;
            ch = (unsigned char)*(*p)++;
            if (ch && strchr("\"\\/bfnrt", ch)) continue;
            if (ch != 'u') return false;
            for (unsigned int i = 0; i < 4; i++)
            {
                if (!**p || !isxdigit((unsigned char)**p)) return false;
                ++*p;
            }
        }
    }
    if (**p != '"') return false;
    ++*p;
    return true;
}

static bool JsonValue(const char **p, unsigned int depth);

static bool JsonObject(const char **p, unsigned int depth)
{
    ++*p;
    JsonSpace(p);
    if (**p == '}') { ++*p; return true; }
    for (;;)
    {
        if (**p != '"' || !JsonString(p)) return false;
        JsonSpace(p);
        if (*(*p)++ != ':') return false;
        JsonSpace(p);
        if (!JsonValue(p, depth + 1)) return false;
        JsonSpace(p);
        if (**p == '}') { ++*p; return true; }
        if (*(*p)++ != ',') return false;
        JsonSpace(p);
    }
}

static bool JsonValue(const char **p, unsigned int depth)
{
    if (depth > 16) return false;
    JsonSpace(p);
    if (**p == '"') return JsonString(p);
    if (**p == '{') return JsonObject(p, depth);
    if (**p == '[')
    {
        ++*p;
        JsonSpace(p);
        if (**p == ']') { ++*p; return true; }
        for (;;)
        {
            if (!JsonValue(p, depth + 1)) return false;
            JsonSpace(p);
            if (**p == ']') { ++*p; return true; }
            if (*(*p)++ != ',') return false;
        }
    }
    const char *literals[] = {"true", "false", "null"};
    for (unsigned int i = 0; i < 3; i++)
    {
        size_t n = strlen(literals[i]);
        if (strncmp(*p, literals[i], n) == 0) { *p += n; return true; }
    }
    const char *start = *p;
    if (**p == '-') ++*p;
    if (**p == '0') ++*p;
    else if (**p >= '1' && **p <= '9')
        while (isdigit((unsigned char)**p)) ++*p;
    else return false;
    if (**p == '.')
    {
        ++*p;
        if (!isdigit((unsigned char)**p)) return false;
        while (isdigit((unsigned char)**p)) ++*p;
    }
    if (**p == 'e' || **p == 'E')
    {
        ++*p;
        if (**p == '+' || **p == '-') ++*p;
        if (!isdigit((unsigned char)**p)) return false;
        while (isdigit((unsigned char)**p)) ++*p;
    }
    return *p != start;
}

static bool HeaderName(const char *line, size_t length, const char *name)
{
    size_t i = 0;
    while (name[i] && i < length &&
           tolower((unsigned char)line[i]) == tolower((unsigned char)name[i])) i++;
    return !name[i] && i < length && line[i] == ':';
}

/* The caller has already established certificate and hostname verified TLS. */
bool Alert_HttpTelegramOK(const char *response)
{
    if (!response || strncmp(response, "HTTP/1.1 200 ", 13) != 0) return false;
    const char *line = strstr(response, "\r\n");
    if (!line) return false;
    line += 2;
    const char *body = strstr(response, "\r\n\r\n");
    if (!body || line > body) return false;
    bool has_length = false;
    size_t declared_length = 0;
    while (line < body)
    {
        const char *end = strstr(line, "\r\n");
        if (!end || end > body) return false;
        if (HeaderName(line, (size_t)(end - line), "Transfer-Encoding")) return false;
        if (HeaderName(line, (size_t)(end - line), "Content-Length"))
        {
            if (has_length) return false;
            has_length = true;
            const char *digits = line + strlen("Content-Length") + 1;
            while (digits < end && (*digits == ' ' || *digits == '\t')) digits++;
            if (digits == end) return false;
            for (; digits < end; digits++)
            {
                if (!isdigit((unsigned char)*digits) || declared_length > 4096U) return false;
                declared_length = declared_length * 10U + (size_t)(*digits - '0');
            }
        }
        line = end + 2;
    }
    body += 4;
    if (has_length && strlen(body) != declared_length) return false;
    JsonSpace(&body);
    if (*body++ != '{') return false;
    bool found = false, value = false;
    JsonSpace(&body);
    if (*body == '}') return false;
    for (;;)
    {
        if (*body != '"') return false;
        const char *key = body + 1;
        if (!JsonString(&body)) return false;
        bool is_ok = body - key == 3 && strncmp(key, "ok", 2) == 0;
        JsonSpace(&body);
        if (*body++ != ':') return false;
        JsonSpace(&body);
        if (is_ok)
        {
            if (found) return false;
            found = true;
            value = strncmp(body, "true", 4) == 0;
        }
        if (!JsonValue(&body, 0)) return false;
        JsonSpace(&body);
        if (*body == '}') { ++body; break; }
        if (*body++ != ',') return false;
        JsonSpace(&body);
    }
    JsonSpace(&body);
    return *body == 0 && found && value;
}
