# Thermal design and ratings notes (sample)

> 데모용으로 직접 작성한 일반 설계 노트입니다. 특정 제조사 문서가 아닙니다.

## Junction temperature
Junction temperature is Tj = Ta + Pd x theta_JA. For a switching converter the power loss is
Pout x (1 / efficiency - 1). For a linear regulator (LDO) the dissipation is (VIN - VOUT) x IOUT + VIN x IQ,
so a large input-to-output difference makes an LDO hot even at moderate current.

## Improving heat dissipation
theta_JA depends strongly on PCB copper area and on thermal vias under the exposed thermal pad.
More copper and more thermal vias lower theta_JA and the junction temperature.

## Absolute maximum ratings
Absolute maximum ratings are stress ratings only. Operating beyond them, even briefly, can cause permanent
damage (electrical overstress, EOS). Recommended operating conditions define the range in which the datasheet
specifications apply. Damage caused by overstress is usually visible as burnt silicon in failure analysis.
