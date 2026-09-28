C_SRCS += \
../ThirdParty/FreeRTOS/tasks.c \
../ThirdParty/FreeRTOS/list.c \
../ThirdParty/FreeRTOS/queue.c \
../ThirdParty/FreeRTOS/portable/GCC/ARM_CM4F/port.c \
../ThirdParty/FreeRTOS/portable/MemMang/heap_4.c

OBJS += \
./ThirdParty/FreeRTOS/tasks.o \
./ThirdParty/FreeRTOS/list.o \
./ThirdParty/FreeRTOS/queue.o \
./ThirdParty/FreeRTOS/portable/GCC/ARM_CM4F/port.o \
./ThirdParty/FreeRTOS/portable/MemMang/heap_4.o

C_DEPS += $(OBJS:%.o=%.d)

ThirdParty/FreeRTOS/%.o: ../ThirdParty/FreeRTOS/%.c ThirdParty/FreeRTOS/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m4 -std=gnu11 -g3 -DDEBUG -DUSE_HAL_DRIVER -DSTM32L4S5xx -c -I../Core/Inc -I../ThirdParty/FreeRTOS/include -I../ThirdParty/FreeRTOS/portable/GCC/ARM_CM4F -I../Drivers/STM32L4xx_HAL_Driver/Inc -I../Drivers/CMSIS/Device/ST/STM32L4xx/Include -I../Drivers/CMSIS/Include -O0 -ffunction-sections -fdata-sections -Wall -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -o "$@"
