#include "status_led.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    unsigned transitions=0;
    bool previous=StatusLed_IsOn(STATUS_LED_FALL,0);
    for (unsigned t=0;t<1000;++t) {
        assert(StatusLed_IsOn(STATUS_LED_SETUP,t));
        assert(StatusLed_IsOn(STATUS_LED_NORMAL,t));
        bool current=StatusLed_IsOn(STATUS_LED_FALL,t);
        if (t && current!=previous) ++transitions;
        previous=current;
    }
    assert(transitions==59);
    assert(!StatusLed_IsOn(STATUS_LED_NORMAL,1000));
    assert(StatusLed_IsOn(STATUS_LED_NORMAL,2000));
    assert(StatusLed_IsOn(STATUS_LED_FALL,1000));
    puts("status LED timing tests passed");
    return 0;
}
