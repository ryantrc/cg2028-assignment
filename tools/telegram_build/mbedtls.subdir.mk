MBEDTLS_SRCS := $(filter-out ../ThirdParty/mbedtls/library/net_sockets.c ../ThirdParty/mbedtls/library/timing.c,$(wildcard ../ThirdParty/mbedtls/library/*.c))
MBEDTLS_OBJS := $(patsubst ../ThirdParty/mbedtls/library/%.c,./ThirdParty/mbedtls/library/%.o,$(MBEDTLS_SRCS))
C_SRCS += $(MBEDTLS_SRCS)
OBJS += $(MBEDTLS_OBJS)
C_DEPS += $(MBEDTLS_OBJS:%.o=%.d)

ThirdParty/mbedtls/library/%.o: ../ThirdParty/mbedtls/library/%.c ThirdParty/mbedtls/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m4 -std=gnu11 -g1 -DDEBUG -DUSE_HAL_DRIVER -DSTM32L4S5xx -c -I../ThirdParty/mbedtls/include -I../ThirdParty/mbedtls/library -I../Core/Inc -I../Drivers/STM32L4xx_HAL_Driver/Inc -I../Drivers/CMSIS/Device/ST/STM32L4xx/Include -I../Drivers/CMSIS/Include -Os -ffunction-sections -fdata-sections -Wall -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -o "$@"
