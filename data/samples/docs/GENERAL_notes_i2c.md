# I2C bus notes (sample)

> 데모용으로 직접 작성한 일반 설계 노트입니다. 특정 제조사 문서가 아닙니다.

## Pull-up resistor sizing
The minimum pull-up resistance is (VDD - VOL(max)) / IOL. The maximum pull-up resistance is
tr / (0.8473 x Cb), where tr is the maximum rise time (300 ns in Fast-mode, 1000 ns in Standard-mode) and Cb is
the total bus capacitance. A pull-up that is too large makes the rising edge too slow, which causes missed bits
and NACKs.

## Address conflicts
Two devices with the same 7-bit address on one bus corrupt each other's responses. Check the address select pins.

## Power-up timing
Do not start I2C transactions before the device has finished its power-up time.
