# EXAMPLE-BUCK1 application notes (sample)

> 이 문서는 데모용으로 직접 작성한 가상 부품의 노트입니다. TI 문서가 아닙니다.
> 실제 데이터시트·에라타·앱노트는 data/raw/docs/ 에 넣고 `python -m fae_agent.cli ingest`를 실행하세요.

## Output oscillation and ringing
Output ringing or oscillation after a load step usually means the control loop has too little phase margin.
Check that the output capacitor value and its ESR are inside the recommended range, and that the compensation
network was recalculated for the output capacitor actually used. Very low ESR ceramic capacitors move the ESR
zero to a higher frequency, so a compensation network designed for a higher-ESR output capacitor may no longer
be stable. Increasing the output capacitance lowers the output impedance sqrt(L/C) and reduces the load-step
deviation.

## Light-load operation
At light load the converter can enter pulse-skipping mode. The switching frequency then changes with load and
the output ripple looks irregular, which is often mistaken for oscillation. Force PWM mode or add a minimum load
to confirm.

## Layout
Keep the feedback (FB) trace short and away from the switch node (SW). Place the feedback divider close to the
FB pin. A long feedback trace next to the switch node couples switching noise into the loop and can cause jitter
or instability.

## Output ripple
Output voltage ripple is approximately delta_IL x ESR + delta_IL / (8 x fsw x Cout). Use low-ESR ceramic
capacitors and increase Cout or the inductance to reduce ripple. A larger inductance lowers the inductor ripple
current delta_IL; a ripple current of 20 to 40 percent of the load current is a common design target.

## Measuring ripple
Measure output ripple across the output capacitor with a short ground spring on the oscilloscope probe.
A long probe ground lead picks up switching noise and shows spikes that are not on the output.

## Output voltage setting
Vout = Vref x (1 + Rtop / Rbottom), with Vref = 0.6 V for this example part. Use 1 percent resistors.
Very large divider resistors make the output sensitive to FB pin leakage current and noise.
If the output drops only under heavy load, check whether the current limit (overcurrent protection) is active.

## Startup
The device starts switching when EN is above its threshold and VIN is above the UVLO threshold. A large output
capacitor with a short soft-start time draws a high inrush current Cout x Vout / t_ss, which can trip the current
limit and cause hiccup mode. A current-limited input supply can also pull VIN below UVLO during startup.
