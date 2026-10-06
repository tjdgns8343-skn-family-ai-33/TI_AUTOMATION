"""답변 초안의 내부 표현 점검(reply_check)과 접수 단계의 새 조건 추출."""

import pytest

from fae_agent.agents.intake import extract_conditions
from fae_agent.reply_check import scrub_reply


def test_file_names_become_datasheet_names_even_with_a_korean_particle():
    text, notices = scrub_reply("근거는 [tps54331.pdf]와 parts/TPS62130A.yaml, tps7a20.pdf를 보세요.")
    assert text == "근거는 [TPS54331 데이터시트]와 TPS62130A 데이터시트, TPS7A20 데이터시트를 보세요."
    assert len(notices) == 1 and notices[0].startswith("답변 초안의 내부 표현 3곳을 고쳤습니다")
    assert "'tps54331.pdf' → 'TPS54331 데이터시트'" in notices[0]


def test_card_item_paths_after_the_file_name_are_removed_too():
    text, _ = scrub_reply("근거: parts/LM358.yaml abs_max.vs_v, parts/TPS54331.yaml")
    assert text == "근거: LM358 데이터시트, TPS54331 데이터시트"


def test_part_card_wording_becomes_datasheet_wording():
    text, _ = scrub_reply("부품 카드 기준으로 3.5 A이고, 카드의 전형값은 570 kHz, 카드 기준 한계는 6 V입니다. case:C-0007 참고")
    assert text == "데이터시트 기준으로 3.5 A이고, 데이터시트의 전형값은 570 kHz, 데이터시트 기준 한계는 6 V입니다. 과거 사례 C-0007 참고"


def test_unrelated_words_with_card_are_left_alone():
    original = "SD카드의 로그와 신용카드, 카드뮴 함량, 그래픽 카드는 관계없습니다. notes.md도 그대로입니다."
    text, notices = scrub_reply(original)
    assert text == original and notices == []


def test_internal_field_names_are_only_reported():
    text, notices = scrub_reply("il_max_a는 3.76 A이고 tj_c도 높습니다. 최대 전류 3.76 A.")
    assert text == "il_max_a는 3.76 A이고 tj_c도 높습니다. 최대 전류 3.76 A."  # 뜻을 모르니 바꾸지 않는다
    assert len(notices) == 1 and "il_max_a, tj_c" in notices[0]


def test_many_changes_are_summarised():
    text, notices = scrub_reply(" ".join(f"tps{n}.pdf" for n in (1, 2, 3, 4, 5, 6, 7)))
    assert "TPS7 데이터시트" in text and "외 2종" in notices[0] and "7곳" in notices[0]


def test_clean_text_is_unchanged():
    text = "문의하신 TPS54331 건입니다. [TPS54331 데이터시트 p.11] 기준 소프트 스타트는 1~10 ms입니다."
    assert scrub_reply(text) == (text, [])


# ---------------------------------------------------------------- 접수 단계 조건 추출


def test_extracts_the_soft_start_capacitor_in_nf_and_uf():
    assert extract_conditions("SS 핀에는 100nF를 달았습니다").css_f == pytest.approx(100e-9)
    assert extract_conditions("소프트 스타트 커패시터 0.1uF 사용").css_f == pytest.approx(0.1e-6)
    assert extract_conditions("Css = 10nF").css_f == pytest.approx(10e-9)
    assert extract_conditions("출력 커패시터 22uF").css_f is None


def test_extracts_feedback_resistors_and_the_measured_case_temperature():
    c = extract_conditions("피드백 저항 100k, 접지 쪽 저항 1k. 열화상 카메라로 보면 칩 표면은 80도 정도입니다.")
    assert c.rf_ohm == pytest.approx(100e3) and c.rg_ohm == pytest.approx(1e3) and c.tcase_meas_c == pytest.approx(80.0)
    assert extract_conditions("케이스 온도는 65°C").tcase_meas_c == pytest.approx(65.0)


def test_ambient_temperature_is_read_in_korean_units_too():
    assert extract_conditions("케이스 안이라 주변 온도는 50도입니다").ta_c == pytest.approx(50.0)
    assert extract_conditions("주변 온도 25°C").ta_c == pytest.approx(25.0)


def test_a_zero_volt_symptom_phrase_is_not_taken_as_the_input_voltage():
    c = extract_conditions("센서 입력을 0V로 접지해도 출력이 나옵니다. 출력이 0V로 떨어집니다. 입력 5V, 출력 3.3V입니다.")
    assert c.vin_v == pytest.approx(5.0) and c.vout_v == pytest.approx(3.3)
    only_zero = extract_conditions("센서 입력을 0V로 접지해도 출력이 나옵니다")
    assert only_zero.vin_v is None
