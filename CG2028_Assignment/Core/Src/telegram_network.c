#include "telegram_network.h"
#include "telegram_secrets.h"
#include "telegram_ca.h"
#include "es_wifi_io.h"
#include "../../Drivers/BSP/Components/es_wifi/es_wifi.h"
#include "FreeRTOS.h"
#include "task.h"
#include "stm32l4xx_hal.h"
#include "mbedtls/ctr_drbg.h"
#include "mbedtls/entropy.h"
#include "mbedtls/platform.h"
#include "mbedtls/platform_time.h"
#include "mbedtls/ssl.h"
#include "mbedtls/x509_crt.h"

#include <stdio.h>
#include <string.h>

#define TELEGRAM_HOST "api.telegram.org"
#define NTP_HOST "time.google.com"
#define TLS_DEADLINE_MS 120000U
#define NTP_UNIX_OFFSET 2208988800UL
#define MIN_VALID_UNIX_TIME 1790467200UL /* 2026-09-27; reject stale replies. */

typedef struct {
    ES_WIFIObject_t wifi;
    RNG_HandleTypeDef rng;
    bool rng_ready;
    bool time_ready;
    uint32_t unix_seconds;
    uint32_t time_tick_ms;
    uint8_t telegram_ip[4];
    void (*log)(const char *);
} NetworkState;

typedef struct {
    NetworkState *network;
    uint32_t started_ms;
    unsigned int sends, receives, bytes_sent, bytes_received;
    int last_send_status, last_receive_status;
} SocketContext;

static NetworkState network;

mbedtls_ms_time_t mbedtls_ms_time(void)
{
    return (mbedtls_ms_time_t)HAL_GetTick();
}

static mbedtls_time_t NetworkTime(mbedtls_time_t *output)
{
    mbedtls_time_t now = (mbedtls_time_t)network.unix_seconds +
        (mbedtls_time_t)((uint32_t)(HAL_GetTick() - network.time_tick_ms) / 1000U);
    if (output) *output = now;
    return now;
}

int mbedtls_hardware_poll(void *data, unsigned char *output,
                          size_t length, size_t *olen)
{
    (void)data;
    *olen = 0;
    if (!network.rng_ready) return -1;
    while (*olen < length)
    {
        uint32_t value;
        if (HAL_RNG_GenerateRandomNumber(&network.rng, &value) != HAL_OK) return -1;
        for (unsigned int i = 0; i < 4 && *olen < length; i++)
            output[(*olen)++] = (unsigned char)(value >> (8U * i));
    }
    return 0;
}

static bool InitRng(void)
{
    uint32_t started = HAL_GetTick();
    __HAL_RCC_HSI48_ENABLE();
    while (!__HAL_RCC_GET_FLAG(RCC_FLAG_HSI48RDY))
        if ((uint32_t)(HAL_GetTick() - started) > 100U) return false;
    __HAL_RCC_RNG_CLK_ENABLE();
    network.rng.Instance = RNG;
    network.rng_ready = HAL_RNG_Init(&network.rng) == HAL_OK;
    return network.rng_ready;
}

static int16_t WifiSend(uint8_t *data, uint16_t length, uint32_t timeout)
{
    return SPI_WIFI_SendData(data, length, timeout);
}

static void Diagnostic(const char *state, int code)
{
    char line[100];
    snprintf(line, sizeof(line), "ALERT Network=%s Code=%d\r\n", state, code);
    network.log(line);
}

static bool InitWifi(void)
{
    ES_WIFI_Status_t status = ES_WIFI_RegisterBusIO(&network.wifi,
        SPI_WIFI_Init, SPI_WIFI_DeInit, SPI_WIFI_Delay, WifiSend, SPI_WIFI_ReceiveData);
    if (status == ES_WIFI_STATUS_OK) status = ES_WIFI_Init(&network.wifi);
    if (status != ES_WIFI_STATUS_OK) { Diagnostic("MODULE_INIT_FAILED", status); return false; }
    char line[132];
    snprintf(line, sizeof(line), "WIFI Product=%.32s FW=%.24s API=%.16s\r\n",
             network.wifi.Product_ID, network.wifi.FW_Rev, network.wifi.API_Rev);
    network.log(line);
    ES_WIFI_SetTimeout(&network.wifi, 15000U);
    return true;
}

static bool ConnectWifi(void)
{
    ES_WIFI_Status_t status = ES_WIFI_Connect(&network.wifi,
        CG2028_WIFI_SSID, CG2028_WIFI_PASSWORD, ES_WIFI_SEC_WPA2);
    if (status != ES_WIFI_STATUS_OK) { Diagnostic("AP_FAILED", status); return false; }
    Diagnostic("AP_CONNECTED", 0);
    status = ES_WIFI_DNS_LookUp(&network.wifi, TELEGRAM_HOST, network.telegram_ip);
    if (status != ES_WIFI_STATUS_OK) { Diagnostic("DNS_FAILED", status); return false; }
    Diagnostic("DNS_OK", 0);
    return true;
}

static uint32_t ReadU32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static bool GetNetworkTime(void)
{
    uint8_t address[4] = {0};
    ES_WIFI_Status_t status = ES_WIFI_DNS_LookUp(&network.wifi, NTP_HOST, address);
    if (status != ES_WIFI_STATUS_OK) { Diagnostic("NTP_DNS_FAILED", status); return false; }
    ES_WIFI_Conn_t socket = {0};
    socket.Type = ES_WIFI_UDP_CONNECTION;
    socket.Number = 1;
    socket.RemotePort = 123;
    memcpy(socket.RemoteIP, address, 4);
    status = ES_WIFI_StartClientConnection(&network.wifi, &socket);
    if (status != ES_WIFI_STATUS_OK) { Diagnostic("NTP_SOCKET_FAILED", status); return false; }

    uint8_t request[48] = {0}, response[64] = {0};
    request[0] = 0x23; /* NTPv4 client. */
    size_t random_length = 0;
    bool success = mbedtls_hardware_poll(NULL, request + 40, 8, &random_length) == 0 &&
                   random_length == 8;
    uint16_t sent = 0, received = 0;
    if (success)
        success = ES_WIFI_SendData(&network.wifi, socket.Number, request,
                    sizeof(request), &sent, 3000U) == ES_WIFI_STATUS_OK && sent == sizeof(request);
    if (success)
        success = ES_WIFI_ReceiveData(&network.wifi, socket.Number, response,
                    sizeof(response), &received, 3000U) == ES_WIFI_STATUS_OK && received >= 48;
    (void)ES_WIFI_StopClientConnection(&network.wifi, &socket);
    if (!success || (response[0] & 7U) != 4U || response[1] == 0 || response[1] > 15 ||
        memcmp(response + 24, request + 40, 8) != 0)
    {
        Diagnostic("NTP_FAILED", (int)received);
        return false;
    }
    uint32_t ntp_seconds = ReadU32(response + 40);
    if (ntp_seconds < NTP_UNIX_OFFSET ||
        ntp_seconds - NTP_UNIX_OFFSET < MIN_VALID_UNIX_TIME)
    {
        Diagnostic("NTP_INVALID_TIME", 0);
        return false;
    }
    network.unix_seconds = ntp_seconds - NTP_UNIX_OFFSET;
    network.time_tick_ms = HAL_GetTick();
    network.time_ready = true;
    mbedtls_platform_set_time(NetworkTime);
    Diagnostic("TIME_READY", 0);
    return true;
}

static int TcpSend(void *context, const unsigned char *data, size_t length)
{
    SocketContext *socket = context;
    if ((uint32_t)(HAL_GetTick() - socket->started_ms) > TLS_DEADLINE_MS) return MBEDTLS_ERR_SSL_TIMEOUT;
    if (length > ES_WIFI_PAYLOAD_SIZE - 1U) length = ES_WIFI_PAYLOAD_SIZE - 1U;
    uint16_t sent = 0;
    ES_WIFI_Status_t result = ES_WIFI_SendData(&socket->network->wifi, 0,
        (uint8_t *)data, (uint16_t)length, &sent, 2000U);
    socket->sends++;
    socket->bytes_sent += sent;
    socket->last_send_status = result;
    return result == ES_WIFI_STATUS_OK && sent ? (int)sent : MBEDTLS_ERR_SSL_INTERNAL_ERROR;
}

static int TcpReceive(void *context, unsigned char *data, size_t length)
{
    SocketContext *socket = context;
    if ((uint32_t)(HAL_GetTick() - socket->started_ms) > TLS_DEADLINE_MS) return MBEDTLS_ERR_SSL_TIMEOUT;
    if (length > ES_WIFI_PAYLOAD_SIZE - 1U) length = ES_WIFI_PAYLOAD_SIZE - 1U;
    uint16_t received = 0;
    ES_WIFI_Status_t result = ES_WIFI_ReceiveData(&socket->network->wifi, 0,
        data, (uint16_t)length, &received, 1000U);
    socket->receives++;
    socket->bytes_received += received;
    socket->last_receive_status = result;
    if (result == ES_WIFI_STATUS_OK && received) return (int)received;
    if (result == ES_WIFI_STATUS_UNEXPECTED_CLOSED_SOCKET) return 0;
    return MBEDTLS_ERR_SSL_WANT_READ;
}

/* An empty request verifies TLS without calling the Bot API. */
static bool VerifiedRequest(const char *request, char *response, size_t response_size)
{
    if (!network.time_ready || !network.rng_ready) return false;
    Diagnostic("TLS_BEGIN", 0);
    ES_WIFI_Conn_t socket = {0};
    socket.Type = ES_WIFI_TCP_CONNECTION;
    socket.Number = 0;
    socket.RemotePort = 443;
    memcpy(socket.RemoteIP, network.telegram_ip, 4);
    ES_WIFI_Status_t result = ES_WIFI_StartClientConnection(&network.wifi, &socket);
    if (result != ES_WIFI_STATUS_OK) { Diagnostic("TCP_FAILED", result); return false; }
    Diagnostic("TCP_CONNECTED", 0);

    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_x509_crt ca;
    mbedtls_ssl_config config;
    mbedtls_ssl_context ssl;
    mbedtls_entropy_init(&entropy);
    mbedtls_ctr_drbg_init(&drbg);
    mbedtls_x509_crt_init(&ca);
    mbedtls_ssl_config_init(&config);
    mbedtls_ssl_init(&ssl);
    const unsigned char personal[] = "CG2028 Telegram TLS";
    int error = mbedtls_ctr_drbg_seed(&drbg, mbedtls_entropy_func, &entropy,
                                      personal, sizeof(personal) - 1U);
    if (!error) error = mbedtls_x509_crt_parse_der(&ca, telegram_root_ca_der,
                                                   telegram_root_ca_der_len);
    if (!error) error = mbedtls_ssl_config_defaults(&config, MBEDTLS_SSL_IS_CLIENT,
                         MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT);
    if (!error)
    {
        mbedtls_ssl_conf_authmode(&config, MBEDTLS_SSL_VERIFY_REQUIRED);
        mbedtls_ssl_conf_ca_chain(&config, &ca, NULL);
        mbedtls_ssl_conf_rng(&config, mbedtls_ctr_drbg_random, &drbg);
        mbedtls_ssl_conf_min_tls_version(&config, MBEDTLS_SSL_VERSION_TLS1_2);
        mbedtls_ssl_conf_max_tls_version(&config, MBEDTLS_SSL_VERSION_TLS1_2);
        error = mbedtls_ssl_setup(&ssl, &config);
    }
    if (!error) error = mbedtls_ssl_set_hostname(&ssl, TELEGRAM_HOST);
    SocketContext io = {.network = &network, .started_ms = HAL_GetTick()};
    if (!error) mbedtls_ssl_set_bio(&ssl, &io, TcpSend, TcpReceive, NULL);
    if (!error) Diagnostic("TLS_HANDSHAKE_START", 0);
    if (!error)
    {
        do {
            error = mbedtls_ssl_handshake(&ssl);
        } while ((error == MBEDTLS_ERR_SSL_WANT_READ ||
                  error == MBEDTLS_ERR_SSL_WANT_WRITE) &&
                 (uint32_t)(HAL_GetTick() - io.started_ms) <= TLS_DEADLINE_MS);
        if (error == MBEDTLS_ERR_SSL_WANT_READ ||
            error == MBEDTLS_ERR_SSL_WANT_WRITE) error = MBEDTLS_ERR_SSL_TIMEOUT;
    }
    if (!error && mbedtls_ssl_get_verify_result(&ssl) != 0) error = MBEDTLS_ERR_X509_CERT_VERIFY_FAILED;
    if (!error) Diagnostic("TLS_VERIFIED", 0);

    if (!error && request)
    {
        size_t total = strlen(request), written = 0;
        while (written < total)
        {
            if ((uint32_t)(HAL_GetTick() - io.started_ms) > TLS_DEADLINE_MS)
            { error = MBEDTLS_ERR_SSL_TIMEOUT; break; }
            int n = mbedtls_ssl_write(&ssl, (const unsigned char *)request + written, total - written);
            if (n == MBEDTLS_ERR_SSL_WANT_READ || n == MBEDTLS_ERR_SSL_WANT_WRITE) continue;
            if (n <= 0) { error = n ? n : MBEDTLS_ERR_SSL_INTERNAL_ERROR; break; }
            written += (size_t)n;
        }
        size_t used = 0;
        if (!error)
        {
            while (used + 1U < response_size)
            {
                if ((uint32_t)(HAL_GetTick() - io.started_ms) > TLS_DEADLINE_MS)
                { error = MBEDTLS_ERR_SSL_TIMEOUT; break; }
                int n = mbedtls_ssl_read(&ssl, (unsigned char *)response + used,
                                          response_size - used - 1U);
                if (n == MBEDTLS_ERR_SSL_WANT_READ || n == MBEDTLS_ERR_SSL_WANT_WRITE) continue;
                if (n == 0 || n == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY) break;
                if (n < 0) { error = n; break; }
                used += (size_t)n;
            }
            response[used] = 0;
            if (used + 1U == response_size) error = MBEDTLS_ERR_SSL_BUFFER_TOO_SMALL;
        }
    }
    mbedtls_ssl_free(&ssl);
    mbedtls_ssl_config_free(&config);
    mbedtls_x509_crt_free(&ca);
    mbedtls_ctr_drbg_free(&drbg);
    mbedtls_entropy_free(&entropy);
    (void)ES_WIFI_StopClientConnection(&network.wifi, &socket);
    if (error)
    {
        char line[150];
        snprintf(line, sizeof(line),
            "ALERT Network=TLS_IO SendCalls=%u SendBytes=%u SendStatus=%d RecvCalls=%u RecvBytes=%u RecvStatus=%d ElapsedMs=%lu\r\n",
            io.sends, io.bytes_sent, io.last_send_status,
            io.receives, io.bytes_received, io.last_receive_status,
            (unsigned long)(HAL_GetTick() - io.started_ms));
        network.log(line);
        Diagnostic("TLS_OR_HTTP_FAILED", error);
    }
    return error == 0;
}

void TelegramNetwork_Run(AlertQueue *alerts, void (*diagnostic)(const char *))
{
    network.log = diagnostic;
    for (;;)
    {
        if (!network.rng_ready && !InitRng())
        { Diagnostic("RNG_FAILED", 0); vTaskDelay(pdMS_TO_TICKS(10000U)); continue; }
        if (!InitWifi() || !ConnectWifi() || !GetNetworkTime())
        {
            vTaskDelay(pdMS_TO_TICKS(10000U));
            continue;
        }
        if (!VerifiedRequest(NULL, NULL, 0))
        {
            vTaskDelay(pdMS_TO_TICKS(10000U));
            continue;
        }
        if (!CG2028_BOT_TOKEN[0] || !CG2028_CHAT_ID[0])
            Diagnostic("CREDENTIALS_INCOMPLETE", 0);
        for (;;)
        {
            AlertItem item;
            bool ready = false;
            taskENTER_CRITICAL();
            const AlertItem *front = AlertQueue_Front(alerts, HAL_GetTick());
            if (front) { item = *front; ready = true; }
            taskEXIT_CRITICAL();
            if (ready && CG2028_BOT_TOKEN[0] && CG2028_CHAT_ID[0])
            {
                char request[800];
                bool delivered = false;
                if (Alert_FormatPost(&item, CG2028_DEVICE_ID,
                    CG2028_BOT_TOKEN, CG2028_CHAT_ID, request, sizeof(request)))
                {
                    static char response[4096];
                    if (VerifiedRequest(request, response, sizeof(response)))
                        delivered = Alert_HttpTelegramOK(response);
                    memset(response, 0, sizeof(response));
                    memset(request, 0, sizeof(request));
                }
                taskENTER_CRITICAL();
                AlertQueue_Result(alerts, delivered, HAL_GetTick());
                taskEXIT_CRITICAL();
                Diagnostic(delivered ? "DELIVERED" : "RETRY_PENDING", delivered ? 0 : 1);
            }
            if (!ES_WIFI_IsConnected(&network.wifi))
            {
                Diagnostic("AP_LOST", 0);
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(500U));
        }
    }
}
