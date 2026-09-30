/******************************************************************************
 * @file           : main.c
 * @brief          : CG2028 Assignment - ElderCare Wearable Safety Companion
 * @author         : Hou Linxin
 * (c) CG2028 Teaching Team
 ******************************************************************************/

/*--------------------------- Includes ---------------------------------------*/
#include "main.h"
#include "motion_metrics.h"
#include "fall_detector.h"
#include "alarm_button.h"
#include "telegram_alerts.h"
#include "telegram_network.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"
#include "../../Drivers/BSP/B-L4S5I-IOT01/stm32l4s5i_iot01.h"
#include "../../Drivers/BSP/B-L4S5I-IOT01/stm32l4s5i_iot01_accelero.h"
#include "../../Drivers/BSP/B-L4S5I-IOT01/stm32l4s5i_iot01_gyro.h"

#include <stdint.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

/*--------------------------- Configuration ----------------------------------*/
#define EWMA_ALPHA_ACCEL_PERCENT 25
#define EWMA_ALPHA_GYRO_PERCENT 25
#define SAMPLE_INTERVAL_MS 100
#define NORMAL_LED_DELAY_MS 1000
#define FALL_LED_DELAY_MS 50
#define LONG_LIE_LED_PERIOD_MS 1000U

static void UART1_Init(void);
static void SystemClock_Config(void);
static void UART_Send(const char *text);
static void SensorTask(void *unused);
static void NetworkTask(void *unused);
static void FormatMetric(char *text, size_t size, bool valid, double value);
static void ResetMotionProcessing(int accel_asm[3], int gyro_asm[3],
                                  int accel_c[3], int gyro_c[3],
                                  MotionMetricsState *accel_history,
                                  MotionMetricsState *gyro_history);
static void ReportDetectorStatus(const FallDetector *detector, FallDetectorEvent event,
                                 uint32_t now_ms, bool sensors_valid,
                                 uint32_t *last_report_ms);

extern int ewma_filter(int new_data, int old_output, int alpha_percent);
int ewma_filter_C(int new_data, int old_output, int alpha_percent);

UART_HandleTypeDef huart1;
volatile uint8_t ES_WIFI_ConnectStage;
static SemaphoreHandle_t uart_mutex;
static AlertQueue alerts;

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    UART1_Init();
    uart_mutex = xSemaphoreCreateMutex();
    AlertQueue_Init(&alerts);
    configASSERT(uart_mutex != NULL);
    configASSERT(xTaskCreate(SensorTask, "sensor", 2048U, NULL, 3U, NULL) == pdPASS);
    configASSERT(xTaskCreate(NetworkTask, "network", 4096U, NULL, 1U, NULL) == pdPASS);
    vTaskStartScheduler();
    for (;;) {}
}

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};

    /* The ST Wi-Fi SPI3 example uses a 10 MHz SPI clock derived from
     * an 80 MHz system clock. The board also needs this speed for TLS. */
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    osc.OscillatorType = RCC_OSCILLATORTYPE_MSI;
    osc.MSIState = RCC_MSI_ON;
    osc.MSICalibrationValue = RCC_MSICALIBRATION_DEFAULT;
    osc.MSIClockRange = RCC_MSIRANGE_6; /* 4 MHz */
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_MSI;
    osc.PLL.PLLM = 1;
    osc.PLL.PLLN = 40;
    osc.PLL.PLLP = RCC_PLLP_DIV7;
    osc.PLL.PLLQ = RCC_PLLQ_DIV2;
    osc.PLL.PLLR = RCC_PLLR_DIV2;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) for (;;) {}

    clk.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                    RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clk.APB1CLKDivider = RCC_HCLK_DIV1;
    clk.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_4) != HAL_OK) for (;;) {}
}

static void NetworkTask(void *unused)
{
    (void)unused;
    TelegramNetwork_Run(&alerts, UART_Send);
}

static void SensorTask(void *unused)
{
    (void)unused;

    BSP_LED_Init(LED2);
    USER_BUTTON_GPIO_CLK_ENABLE();
    GPIO_InitTypeDef button_gpio = {0};
    button_gpio.Pin = USER_BUTTON_PIN;
    button_gpio.Mode = GPIO_MODE_INPUT;
    button_gpio.Pull = GPIO_NOPULL; /* BSP board supplies the button bias. */
    HAL_GPIO_Init(USER_BUTTON_GPIO_PORT, &button_gpio);
    /* The BSP can leave a driver uninitialized after a failed sensor-ID read.
     * Check both return values AND errors hidden by its I2C recovery routine. */
    uint32_t init_errors = BSP_SENSOR_IO_GetErrorCount();
    ACCELERO_StatusTypeDef accel_status = BSP_ACCELERO_Init();
    GYRO_StatusTypeDef gyro_status = BSP_GYRO_Init();
    bool sensors_ready = (accel_status == ACCELERO_OK) && (gyro_status == GYRO_OK)
                      && (BSP_SENSOR_IO_GetErrorCount() == init_errors);
    BSP_LED_Off(LED2);

    /* Previous EWMA outputs. The first test/application sample starts from 0. */
    int accel_ewma_asm[3] = {0, 0, 0};
    int gyro_ewma_asm[3] = {0, 0, 0};

    /* Reference C states are kept separately for assembly verification. */
    int accel_ewma_c[3] = {0, 0, 0};
    int gyro_ewma_c[3] = {0, 0, 0};

    unsigned long sample_number = 0;
    MotionMetricsState accel_metrics_state = {0};
    MotionMetricsState gyro_metrics_state = {0};
    uint32_t last_sample_ms = HAL_GetTick() - SAMPLE_INTERVAL_MS;
    uint32_t last_led_ms = HAL_GetTick();
    uint32_t last_report_ms = HAL_GetTick() - 1000U;
    uint32_t previous_read_ms = 0;
    bool has_previous_read = false;
    /* Keep the three-second history buffer out of the main call stack. */
    static FallDetector detector;
    FallDetector_Init(&detector);
    UART_Send("ALERT Delivery=PENDING Network=STARTING\r\n");
    AlarmButton button;
    AlarmButton_Init(&button,
        HAL_GPIO_ReadPin(USER_BUTTON_GPIO_PORT, USER_BUTTON_PIN) == GPIO_PIN_RESET,
        HAL_GetTick());

    while (1)
    {
        /* LED timing is independent of sensor sampling: a slow blink must
         * not stop sensor reads for a whole second. */
        uint32_t now_ms = HAL_GetTick();
        bool pressed = HAL_GPIO_ReadPin(USER_BUTTON_GPIO_PORT, USER_BUTTON_PIN) == GPIO_PIN_RESET;
        if (AlarmButton_Update(&button, pressed, detector.fall_latched, now_ms))
        {
            bool fault_before_reset = detector.sensor_fault_active || !sensors_ready;
            FallDetector_ManualReset(&detector);
            taskENTER_CRITICAL();
            AlertQueue_ResetEpisode(&alerts);
            taskEXIT_CRITICAL();
            ResetMotionProcessing(accel_ewma_asm, gyro_ewma_asm,
                                  accel_ewma_c, gyro_ewma_c,
                                  &accel_metrics_state, &gyro_metrics_state);
            has_previous_read = false;
            BSP_LED_Off(LED2);
            last_led_ms = now_ms;
            if (fault_before_reset)
            {
                FallDetectorInput faulty = {.time_ms = now_ms, .valid = false};
                (void)FallDetector_Update(&detector, &faulty);
            }
            char reset_message[160];
            snprintf(reset_message, sizeof(reset_message),
                     "DETECTOR TimeMs=%lu State=%s Alarm=0 Sensors=%s Event=MANUAL_RESET Resetting alarm; collecting baseline.\r\n",
                     (unsigned long)now_ms, FallDetector_StateName(detector.state),
                     fault_before_reset ? "FAULT" : "OK");
            UART_Send(reset_message);
        }
        if (detector.state == FALL_STATE_LONG_LIE)
        {
            /* Two short flashes per second distinguish escalation from fall. */
            uint32_t phase = now_ms % LONG_LIE_LED_PERIOD_MS;
            if (phase < 100U || (phase >= 200U && phase < 300U)) BSP_LED_On(LED2);
            else BSP_LED_Off(LED2);
        }
        else
        {
        uint32_t led_interval_ms = detector.fall_latched ? FALL_LED_DELAY_MS : NORMAL_LED_DELAY_MS;
        if ((uint32_t)(now_ms - last_led_ms) >= led_interval_ms)
        {
            BSP_LED_Toggle(LED2);
            last_led_ms = now_ms;
        }
        }
        if ((uint32_t)(now_ms - last_sample_ms) < SAMPLE_INTERVAL_MS)
        {
            vTaskDelay(pdMS_TO_TICKS(1U));
            continue;
        }

        int16_t accel_raw_i16[3] = {0, 0, 0};
        float gyro_raw_float[3] = {0.0f, 0.0f, 0.0f};
        int gyro_raw_int[3] = {0, 0, 0};

        /* Timestamp the start of this pair of sensor reads. Restart scheduling
         * from the actual acquisition time, without bursts to catch up after
         * a debug pause or a slow iteration. Slopes use the actual tick times. */
        uint32_t sample_time_ms = HAL_GetTick();
        last_sample_ms = sample_time_ms;
        bool sample_gap = has_previous_read &&
            (uint32_t)(sample_time_ms - previous_read_ms) > FALL_MAX_SAMPLE_GAP_MS;
        previous_read_ms = sample_time_ms;
        has_previous_read = true;

        if (sample_gap)
        {
            /* Do not compute an axis difference across a debug pause/read gap. */
            ResetMotionProcessing(accel_ewma_asm, gyro_ewma_asm,
                                  accel_ewma_c, gyro_ewma_c,
                                  &accel_metrics_state, &gyro_metrics_state);
        }

        bool sample_valid = sensors_ready;
        if (sensors_ready)
        {
            uint32_t read_errors = BSP_SENSOR_IO_GetErrorCount();
            BSP_ACCELERO_AccGetXYZ(accel_raw_i16);
            BSP_GYRO_GetXYZ(gyro_raw_float);
            if (BSP_SENSOR_IO_GetErrorCount() != read_errors)
            {
                /* Require a board reset/reinitialization after a bus failure.
                 * Restarting the Python recorder cannot repair sensor setup. */
                sensors_ready = false;
                sample_valid = false;
            }
        }

        bool all_axes_zero = true;
        for (int axis = 0; axis < 3; axis++)
        {
            if (accel_raw_i16[axis] != 0 || gyro_raw_float[axis] != 0.0f)
                all_axes_zero = false;
            /* Check before converting a float to int. Invalid readings must
             * never become false evidence of stillness or enter the EWMA. */
            if (!isfinite(gyro_raw_float[axis]) ||
                gyro_raw_float[axis] >= (float)INT_MAX ||
                gyro_raw_float[axis] <= (float)INT_MIN)
            {
                sample_valid = false;
                sensors_ready = false;
            }
        }
        if (all_axes_zero ||
            (uint32_t)(HAL_GetTick() - sample_time_ms) > FALL_MAX_SAMPLE_GAP_MS)
            sample_valid = false;

        FallDetectorInput detector_input = {0};
        detector_input.time_ms = sample_time_ms;
        detector_input.valid = sample_valid;
        if (!sample_valid)
        {
            ResetMotionProcessing(accel_ewma_asm, gyro_ewma_asm,
                                  accel_ewma_c, gyro_ewma_c,
                                  &accel_metrics_state, &gyro_metrics_state);
            FallDetectorEvent event = FallDetector_Update(&detector, &detector_input);
            ReportDetectorStatus(&detector, event, HAL_GetTick(), false, &last_report_ms);
            /* A missing measurement is not a zero measurement: do not send
             * a Sample/Accel/Gyro frame that the recorder could save as data. */
            sample_number++;
            continue;
        }

        /* The supplied BSP reports gyroscope readings as floating-point raw
         * values. Convert them to signed integers before passing them to the
         * integer assembly routine. */
        for (int axis = 0; axis < 3; axis++)
        {
            gyro_raw_int[axis] = (int)gyro_raw_float[axis];
            // convert float to int, truncating the decimal part

            accel_ewma_asm[axis] = ewma_filter(
                (int)accel_raw_i16[axis],
                accel_ewma_asm[axis],
                EWMA_ALPHA_ACCEL_PERCENT);

            gyro_ewma_asm[axis] = ewma_filter(
                gyro_raw_int[axis],
                gyro_ewma_asm[axis],
                EWMA_ALPHA_GYRO_PERCENT);

            /* get accelerometer and gyroscope filtered values */

            accel_ewma_c[axis] = ewma_filter_C(
                (int)accel_raw_i16[axis],
                accel_ewma_c[axis],
                EWMA_ALPHA_ACCEL_PERCENT);

            gyro_ewma_c[axis] = ewma_filter_C(
                gyro_raw_int[axis],
                gyro_ewma_c[axis],
                EWMA_ALPHA_GYRO_PERCENT);

            /* verify with C implementation */
        }

        /* Accelerometer filtered readings are in meters per second squared. */
        float accel_mps2[3] = {
            accel_ewma_asm[0] * (9.80665f / 1000.0f),
            accel_ewma_asm[1] * (9.80665f / 1000.0f),
            accel_ewma_asm[2] * (9.80665f / 1000.0f)};

        /* Gyroscope filtered readings are in degrees per second. */
        float gyro_dps[3] = {
            gyro_ewma_asm[0] / 1000.0f,
            gyro_ewma_asm[1] / 1000.0f,
            gyro_ewma_asm[2] / 1000.0f};

        /* Magnitude = sqrt(X*X + Y*Y + Z*Z) of the current filtered axes.
         * MSD remains the mean of the three squared AXIS changes since the
         * previous sample; it is not the squared change in magnitude.
         * MagnitudeSlope and MSDSlope fit a straight line to their latest five
         * readings against actual sample times. Positive slopes mean rising
         * values; negative slopes mean falling values. Startup fields remain
         * NA until their complete five-point windows are available.
         * Acceleration MSD uses (m/s^2)^2; gyro MSD uses (degrees/s)^2.
         * Each corresponding slope adds a further division by seconds. */
        MotionMetrics accel_metrics = MotionMetrics_Update(
            &accel_metrics_state, accel_mps2, sample_time_ms);
        MotionMetrics gyro_metrics = MotionMetrics_Update(
            &gyro_metrics_state, gyro_dps, sample_time_ms);

        /* Prototype 3: every valid spike keeps the original [5,6), [6,7),
         * [7,8) checks, extending if uncertain. The first two seconds versus
         * the preceding three-second baseline qualify the NEAR_FALL label
         * only: a low ratio cannot veto a later possible fall. Continued
         * movement with a low ratio is neutral MOVEMENT_CONTINUED.
         * Both sensors must agree on motion/stillness; a fall stays latched. */
        detector_input.msd_valid = accel_metrics.msd_valid && gyro_metrics.msd_valid;
        detector_input.accel_msd = accel_metrics.msd;
        detector_input.gyro_msd = gyro_metrics.msd;
        detector_input.gyro_magnitude = gyro_metrics.magnitude;
        FallDetectorEvent detector_event = FallDetector_Update(&detector, &detector_input);
        if (detector_event == FALL_EVENT_FALL || detector_event == FALL_EVENT_LONG_LIE)
        {
            BSP_LED_On(LED2);
            last_led_ms = HAL_GetTick();
            AlertKind kind = detector_event == FALL_EVENT_FALL ? ALERT_FALL : ALERT_LONG_LIE;
            taskENTER_CRITICAL();
            bool queued = AlertQueue_Event(&alerts, kind, sample_time_ms);
            unsigned int queue_count = alerts.count;
            unsigned int dropped_count = alerts.dropped;
            taskEXIT_CRITICAL();
            if (queued)
            {
                char alert_status[96];
                snprintf(alert_status, sizeof(alert_status),
                         "ALERT TimeMs=%lu Kind=%s Delivery=PENDING Queue=%u Dropped=%u\r\n",
                         (unsigned long)sample_time_ms,
                         kind == ALERT_FALL ? "FALL" : "LONG_LIE",
                         queue_count, dropped_count);
                UART_Send(alert_status);
            }
        }

        char accel_msd[24], accel_magnitude_slope[24], accel_msd_slope[24];
        char gyro_msd[24], gyro_magnitude_slope[24], gyro_msd_slope[24];
        FormatMetric(accel_msd, sizeof(accel_msd), accel_metrics.msd_valid, accel_metrics.msd);
        FormatMetric(accel_magnitude_slope, sizeof(accel_magnitude_slope), accel_metrics.magnitude_slope_valid, accel_metrics.magnitude_slope);
        FormatMetric(accel_msd_slope, sizeof(accel_msd_slope), accel_metrics.msd_slope_valid, accel_metrics.msd_slope);
        FormatMetric(gyro_msd, sizeof(gyro_msd), gyro_metrics.msd_valid, gyro_metrics.msd);
        FormatMetric(gyro_magnitude_slope, sizeof(gyro_magnitude_slope), gyro_metrics.magnitude_slope_valid, gyro_metrics.magnitude_slope);
        FormatMetric(gyro_msd_slope, sizeof(gyro_msd_slope), gyro_metrics.msd_slope_valid, gyro_metrics.msd_slope);

        char buffer[512];
        snprintf(buffer, sizeof(buffer),
                 "Sample %lu TimeMs=%lu SlopeWindow=%u\r\n"
                 "Accel EWMA ASM [m/s^2]: X=%8.3f Y=%8.3f Z=%8.3f Magnitude=%8.3f MSD=%s MagnitudeSlope=%s MSDSlope=%s\r\n"
                 "Gyro  EWMA ASM [dps]  : X=%8.3f Y=%8.3f Z=%8.3f Magnitude=%8.3f MSD=%s MagnitudeSlope=%s MSDSlope=%s\r\n",
                 sample_number, (unsigned long)sample_time_ms, MOTION_SLOPE_WINDOW_SAMPLES,
                 accel_mps2[0], accel_mps2[1], accel_mps2[2], accel_metrics.magnitude,
                 accel_msd, accel_magnitude_slope, accel_msd_slope,
                 gyro_dps[0], gyro_dps[1], gyro_dps[2], gyro_metrics.magnitude,
                 gyro_msd, gyro_magnitude_slope, gyro_msd_slope);
        UART_Send(buffer);

        /* Optional debugging check. This confirms that the assembly routine
         * matches the reference C routine for the current samples. */
        if ((accel_ewma_asm[0] != accel_ewma_c[0]) ||
            (accel_ewma_asm[1] != accel_ewma_c[1]) ||
            (accel_ewma_asm[2] != accel_ewma_c[2]) ||
            (gyro_ewma_asm[0] != gyro_ewma_c[0]) ||
            (gyro_ewma_asm[1] != gyro_ewma_c[1]) ||
            (gyro_ewma_asm[2] != gyro_ewma_c[2]))
        {
            UART_Send("WARNING: Assembly and C EWMA outputs do not match.\r\n");
        }

        /* Diagnostics are separate lines so the existing 22-column recording
         * format remains unchanged. The one-second status also helps a serial
         * viewer opened after an event see a latched alarm or a sensor fault. */
        ReportDetectorStatus(&detector, detector_event, HAL_GetTick(), true, &last_report_ms);

        sample_number++;
    }
}

static void ResetMotionProcessing(int accel_asm[3], int gyro_asm[3],
                                  int accel_c[3], int gyro_c[3],
                                  MotionMetricsState *accel_history,
                                  MotionMetricsState *gyro_history)
{
    memset(accel_asm, 0, 3 * sizeof(int));
    memset(gyro_asm, 0, 3 * sizeof(int));
    memset(accel_c, 0, 3 * sizeof(int));
    memset(gyro_c, 0, 3 * sizeof(int));
    memset(accel_history, 0, sizeof(*accel_history));
    memset(gyro_history, 0, sizeof(*gyro_history));
}

static void ReportDetectorStatus(const FallDetector *detector, FallDetectorEvent event,
                                 uint32_t now_ms, bool sensors_valid,
                                 uint32_t *last_report_ms)
{
    if (event == FALL_EVENT_NONE && (uint32_t)(now_ms - *last_report_ms) < 1000U)
        return;
    *last_report_ms = now_ms;

    const char *name = "STATUS";
    const char *message = "";
    switch (event)
    {
    case FALL_EVENT_READY:
        name = "READY"; message = "Monitoring movement."; break;
    case FALL_EVENT_SPIKE:
        name = "SPIKE"; message = "Spike; observing movement and stillness."; break;
    case FALL_EVENT_DISTURBANCE_CONFIRMED:
        name = "DISTURBANCE_CONFIRMED"; message = "Unusual disturbance; continuing original observation."; break;
    case FALL_EVENT_DISTURBANCE_REJECTED:
        name = "DISTURBANCE_REJECTED"; message = "Increase below threshold; monitoring resumes."; break;
    case FALL_EVENT_DISTURBANCE_LOW:
        name = "DISTURBANCE_LOW"; message = "Increase below threshold; fall observation continues."; break;
    case FALL_EVENT_DISTURBANCE_UNKNOWN:
        name = "DISTURBANCE_UNKNOWN"; message = "Insufficient history/coverage; collecting baseline (~5 seconds)."; break;
    case FALL_EVENT_NEAR_FALL:
        name = "NEAR_FALL"; message = "Strong disturbance followed by continued movement; monitoring resumes."; break;
    case FALL_EVENT_MOVEMENT_CONTINUED:
        name = "MOVEMENT_CONTINUED"; message = "Continued movement without a qualifying relative increase; monitoring resumes."; break;
    case FALL_EVENT_FALL:
        name = "POSSIBLE_FALL"; message = "Sustained stillness; hold user button for two seconds to clear alarm."; break;
    case FALL_EVENT_LONG_LIE:
        name = "LONG_LIE"; message = "Thirty complete quiet seconds after fall (experimental demo setting)."; break;
    case FALL_EVENT_UNCERTAIN:
        name = "UNCERTAIN"; message = "Mixed evidence; continuing one-second checks."; break;
    case FALL_EVENT_SENSOR_FAULT:
        name = "SENSOR_FAULT"; message = "Invalid sensor data; check connection and reset board."; break;
    case FALL_EVENT_RESTARTED:
        name = "RESTARTED"; message = "Sampling interrupted/recovered; collecting baseline (~5 seconds)."; break;
    case FALL_EVENT_NONE:
        break;
    }
    char gate_metrics[112] = "";
    if (event == FALL_EVENT_DISTURBANCE_CONFIRMED ||
        event == FALL_EVENT_DISTURBANCE_LOW ||
        event == FALL_EVENT_DISTURBANCE_REJECTED)
    {
        snprintf(gate_metrics, sizeof(gate_metrics),
                 " BaselineMSD=%.6e EventMSD=%.6e IncreaseRatio=%.6e",
                 detector->candidate_baseline_mean, detector->event_accel_msd_mean,
                 detector->disturbance_ratio);
    }
    char text[384];
    snprintf(text, sizeof(text),
             "DETECTOR TimeMs=%lu State=%s Alarm=%u Sensors=%s Event=%s %s%s\r\n",
             (unsigned long)now_ms, FallDetector_StateName(detector->state),
             detector->fall_latched ? 1U : 0U, sensors_valid ? "OK" : "FAULT", name, message,
             gate_metrics);
    UART_Send(text);
}

static void FormatMetric(char *text, size_t size, bool valid, double value)
{
    if (valid)
    {
        /* Scientific notation preserves small squared changes that would
         * disappear if rounded to six decimal places in fixed notation. */
        snprintf(text, size, "%.6e", value);
    }
    else
    {
        snprintf(text, size, "NA");
    }
}

int ewma_filter_C(int new_data, int old_output, int alpha_percent)
{
    /* Reference implementation for verification only. The assembly routine
     * must be used in the actual sensor-processing and detection pipeline. */
    int numerator = alpha_percent * new_data + (100 - alpha_percent) * old_output;
    return numerator / 100;
}

static void UART_Send(const char *text)
{
    bool locked = xTaskGetSchedulerState() == taskSCHEDULER_RUNNING &&
                  xSemaphoreTake(uart_mutex, pdMS_TO_TICKS(50U)) == pdTRUE;
    if (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING && !locked) return;
    HAL_UART_Transmit(&huart1, (uint8_t *)text, strlen(text), HAL_MAX_DELAY);
    if (locked) xSemaphoreGive(uart_mutex);
}

void vApplicationStackOverflowHook(TaskHandle_t task, char *name)
{
    (void)task; (void)name;
    __disable_irq();
    for (;;) {}
}

void vApplicationMallocFailedHook(void)
{
    __disable_irq();
    for (;;) {}
}

static void UART1_Init(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_USART1_CLK_ENABLE();

    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Alternate = GPIO_AF7_USART1;
    GPIO_InitStruct.Pin = GPIO_PIN_7 | GPIO_PIN_6;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    huart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart1) != HAL_OK)
    {
        while (1)
        {
        }
    }
}

/* Do not modify these lines. They suppress UART-related warnings. */
int _write(int file, char *ptr, int len)
{
    (void)file;
    (void)ptr;
    return len;
}
int _read(int file, char *ptr, int len)
{
    (void)file;
    (void)ptr;
    (void)len;
    return 0;
}
int _fstat(int file, struct stat *st)
{
    (void)file;
    (void)st;
    return 0;
}
int _lseek(int file, int ptr, int dir)
{
    (void)file;
    (void)ptr;
    (void)dir;
    return 0;
}
int _isatty(int file)
{
    (void)file;
    return 1;
}
int _close(int file)
{
    (void)file;
    return -1;
}
int _getpid(void) { return 1; }
int _kill(int pid, int sig)
{
    (void)pid;
    (void)sig;
    return -1;
}
