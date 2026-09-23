/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file           : main.c
 * @brief          : Main program body
 ******************************************************************************
 * @attention
 *
 * <h2><center>&copy; Copyright (c) 2020 STMicroelectronics.
 * All rights reserved.</center></h2>
 *
 * This software component is licensed by ST under BSD 3-Clause license,
 * the "License"; You may not use this file except in compliance with the
 * License. You may obtain a copy of the License at:
 *                        opensource.org/licenses/BSD-3-Clause
 *
 ******************************************************************************
 */
/* USER CODE END Header */

#include "main.h"
#include "stdio.h"
#include "../../Drivers/BSP/B-L4S5I-IOT01/stm32l4s5i_iot01.h"
static void MX_GPIO_Init(void);
static void BSP_GPIO_Init(void);
extern void initialise_monitor_handles(void); // for semi-hosting support (printf)

int start_time = 0;

int main(void)
{
  HAL_Init();
  // MX_GPIO_Init(); // Original HAL setup, kept below for comparison.
  BSP_GPIO_Init();
  initialise_monitor_handles(); // for semi-hosting support (printf)

  /* Previous HAL version (toggled the LED every 50 ms):
  while (1)
  {
      HAL_GPIO_TogglePin(GPIOB, GPIO_PIN_14);
      HAL_Delay(50);
  }
  */

  while (1)
  {
    BSP_LED_Toggle(LED2);
    HAL_Delay(1000); // Toggle once every second.
  }
}

static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, LED2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin LED2_Pin */
  GPIO_InitStruct.Pin = LED2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}

/* Configure the same LED using the board support library. */
static void BSP_GPIO_Init(void)
{
  BSP_LED_Init(LED2);
  BSP_LED_Off(LED2); // Start with the LED off.
}
