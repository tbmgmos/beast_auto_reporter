from src.conclusion_generator import ConclusionGenerator
from src.csv_importer import Issue


def test_conclusion_generator_reads_llm_settings_from_config():
    generator = ConclusionGenerator(config={
        "llm": {
            "model": "test-model",
            "temperature": 0.33,
            "max_tokens": 777,
            "timeout": 12,
            "ollama_host": "http://127.0.0.1:11434",
        }
    })

    assert generator.llm_model == "test-model"
    assert generator.llm_temperature == 0.33
    assert generator.llm_max_tokens == 777
    assert generator.llm_timeout == 12
    assert generator.ollama_host == "http://127.0.0.1:11434"


def test_structured_llm_output_is_formatted_into_final_conclusion():
    generator = ConclusionGenerator()
    raw = """
    {
      "title": "По субъективной оценке выявлены следующие недочёты:",
      "items": [
        {
          "kind": "blocker",
          "timecodes": ["01:00:49:06"],
          "omit_timecode": false,
          "text": "видеофайл не синхронен со звуковыми дорожками (смещение ~8 кадров)"
        },
        {
          "kind": "general",
          "timecodes": [],
          "omit_timecode": true,
          "text": "звуковые дорожки не синхронны с изображением на 8 кадров"
        }
      ]
    }
    """

    conclusion = generator._format_structured_llm_output(raw)

    assert conclusion is not None
    assert "-    На таймкоде 01:00:49:06 видеофайл не синхронен со звуковыми дорожками (смещение ~8 кадров)" in conclusion
    assert "-    Звуковые дорожки не синхронны с изображением на 8 кадров" in conclusion


def test_structured_llm_output_formats_multiple_timecodes():
    generator = ConclusionGenerator()
    raw = """
    {
      "title": "По субъективной оценке выявлены следующие недочёты:",
      "items": [
        {
          "kind": "specific",
          "timecodes": ["01:11:42:06", "01:11:44:22", "01:12:00:00"],
          "omit_timecode": false,
          "text": "реплики несинхронны с изображением"
        }
      ]
    }
    """

    conclusion = generator._format_structured_llm_output(raw)

    assert conclusion is not None
    assert "На таймкодах 01:11:42:06, 01:11:44:22 и 01:12:00:00 реплики несинхронны с изображением" in conclusion


def test_structured_llm_output_returns_none_for_non_json_text():
    generator = ConclusionGenerator()

    assert generator._format_structured_llm_output("обычный текст без json") is None


def test_structured_llm_output_rejects_wrong_kind_order_against_python_contract():
    generator = ConclusionGenerator()
    blockers = [
        Issue(
            "01:00:49:06",
            "",
            "Видео не синхронно со звуковыми дорожками",
            True,
            True,
            False,
            False,
            False,
            True,
            False,
        )
    ]
    groups = {
        "другие_проблемы": [
            Issue(
                "01:12:29:05",
                "",
                "Слышна склейка фонового шума моря",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            )
        ],
        "щелчки_слюна": [
            Issue(
                "01:20:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:21:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:22:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:23:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
        ],
    }
    raw = """
    {
      "title": "По субъективной оценке выявлены следующие недочёты:",
      "items": [
        {
          "kind": "specific",
          "timecodes": ["01:12:29:05"],
          "omit_timecode": false,
          "text": "слышна склейка фонового шума моря"
        },
        {
          "kind": "blocker",
          "timecodes": ["01:00:49:06"],
          "omit_timecode": false,
          "text": "видео не синхронно со звуковыми дорожками"
        },
        {
          "kind": "general",
          "timecodes": [],
          "omit_timecode": true,
          "text": "в фонограмме присутствуют посторонние щёлкающие звуки"
        }
      ]
    }
    """

    conclusion = generator._format_structured_llm_output(raw, blockers, groups, "main")

    assert conclusion is None


def test_structured_llm_output_rejects_unknown_kind():
    generator = ConclusionGenerator()
    raw = """
    {
      "title": "По субъективной оценке выявлены следующие недочёты:",
      "items": [
        {
          "kind": "summary",
          "timecodes": [],
          "omit_timecode": true,
          "text": "в фонограмме присутствуют посторонние щёлкающие звуки"
        }
      ]
    }
    """

    assert generator._format_structured_llm_output(raw) is None


def test_structured_llm_output_rejects_timecodes_that_do_not_match_python_contract():
    generator = ConclusionGenerator()
    groups = {
        "другие_проблемы": [
            Issue(
                "01:12:29:05",
                "",
                "Слышна склейка фонового шума моря",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            )
        ]
    }
    raw = """
    {
      "title": "По субъективной оценке выявлены следующие недочёты:",
      "items": [
        {
          "kind": "specific",
          "timecodes": ["01:12:29:06"],
          "omit_timecode": false,
          "text": "слышна склейка фонового шума моря"
        }
      ]
    }
    """

    conclusion = generator._format_structured_llm_output(raw, [], groups, "main")

    assert conclusion is None


def test_clean_marker_description_normalizes_me_noise_wording():
    raw = "С этого момента уровень фонового шума резко становится громче"

    assert (
        ConclusionGenerator._clean_marker_description(raw)
        == "резко усиливается фоновый шум"
    )


def test_clean_marker_description_normalizes_level_change_variants():
    raw = "Уровень звуковой атмосферы становится тише"

    assert (
        ConclusionGenerator._clean_marker_description(raw)
        == "ослабевает звуковая атмосфера"
    )


def test_me_group_summary_uses_dominant_marker_context():
    generator = ConclusionGenerator()
    items = [
        Issue("00:00:01:00", "", "С этого момента уровень фонового шума резко становится громче", False, False, False, False, False, True, False),
        Issue("00:00:02:00", "", "Уровень фонового шума резко становится громче", False, False, False, False, False, True, False),
        Issue("00:00:03:00", "", "В этом фрагменте уровень фонового шума резко становится громче", False, False, False, False, False, True, False),
    ]

    assert (
        generator._format_me_issue_line("атмосфера", items, "me")
        == "В нескольких фрагментах резко усиливается фоновый шум"
    )


def test_smart_group_issues_splits_mixed_background_noise_contexts():
    generator = ConclusionGenerator()
    issues = [
        Issue("01:05:41:09", "", "С этого момента уровень фонового шума резко становится громче", False, False, False, False, False, True, False),
        Issue("01:17:42:09", "", "С этого момента резко возрастает громкость фонового шума", False, False, False, False, False, True, False),
        Issue("01:05:51:15", "", "С этого момента резко меняется звучание фонового шума", False, False, False, False, False, True, False),
        Issue("01:12:25:03", "", "С этого момента резко меняется звучание фонового шума", False, False, False, False, False, True, False),
        Issue("01:11:13:20", "", "С этого момента резко пропадант слой фонового шума", False, False, False, False, False, True, False),
        Issue("01:19:40:17", "", "С этого момента резко меняется звучание фоновго шума ( он становится тише)", False, False, False, False, False, True, False),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert set(groups) >= {
        "атмосфера__noise_up",
        "атмосфера__noise_change",
        "атмосфера__noise_down",
    }
    assert len(groups["атмосфера__noise_up"]) == 2
    assert len(groups["атмосфера__noise_change"]) == 2
    assert len(groups["атмосфера__noise_down"]) == 2


def test_me_conclusion_does_not_merge_opposite_background_noise_changes():
    generator = ConclusionGenerator()
    issues = [
        Issue("01:05:41:09", "", "С этого момента уровень фонового шума резко становится громче", False, False, False, False, False, True, False),
        Issue("01:17:42:09", "", "С этого момента резко возрастает громкость фонового шума", False, False, False, False, False, True, False),
        Issue("01:05:51:15", "", "С этого момента резко меняется звучание фонового шума", False, False, False, False, False, True, False),
        Issue("01:12:25:03", "", "С этого момента резко меняется звучание фонового шума", False, False, False, False, False, True, False),
        Issue("01:11:13:20", "", "С этого момента резко пропадант слой фонового шума", False, False, False, False, False, True, False),
        Issue("01:19:40:17", "", "С этого момента резко меняется звучание фоновго шума ( он становится тише)", False, False, False, False, False, True, False),
    ]

    groups = generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "В некоторых фрагментах меняется звучание фонового шума" in conclusion
    assert "усиливается фоновый шум" not in conclusion
    assert "ослабевает или пропадает слой фонового шума" not in conclusion


def test_manual_style_examples_block_loads_for_me_and_main():
    generator = ConclusionGenerator()

    me_block = generator._build_manual_style_examples_block("me")
    main_block = generator._build_manual_style_examples_block("main")

    assert "ПРИНЦИПЫ, ВЫВЕДЕННЫЕ ИЗ РУЧНЫХ DOCX-ЗАКЛЮЧЕНИЙ:" in me_block
    assert "Сохраняй конкретику маркера" in me_block
    assert "Обобщай только действительно повторяющуюся или системную проблему" in main_block
    assert "смысловую группу проблемы" in main_block


def test_smart_group_issues_groups_music_duplication_despite_marker_typos():
    generator = ConclusionGenerator()
    issues = [
        Issue("01:05:15:21", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, False, False, False, False, True, False),
        Issue("01:08:54:12", "", "Музыка задвоилась. В предыдущей версии такого не было", False, False, False, False, False, True, False),
        Issue("01:25:00:20", "", "Музыка неммного задвоилась. В предыдущей версии такого не было", False, False, False, False, False, True, False),
    ]

    groups = generator._smart_group_issues(issues, "main")

    assert "задвоение_музыки" in groups
    assert len(groups["задвоение_музыки"]) == 3
    assert "другие_проблемы" not in groups


def test_main_conclusion_generalizes_music_duplication_variants_into_one_point():
    generator = ConclusionGenerator()
    issues = [
        Issue("01:05:15:21", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, False, False, False, False, True, False),
        Issue("01:08:54:12", "", "Музыка задвоилась. В предыдущей версии такого не было", False, False, False, False, False, True, False),
        Issue("01:25:00:20", "", "Музыка неммного задвоилась. В предыдущей версии такого не было", False, False, False, False, False, True, False),
    ]

    groups = generator._smart_group_issues(issues, "main")
    conclusion = generator._python_format_conclusion([], groups, "main")

    assert "В нескольких фрагментах в музыке слышится задвоение или параллельная посторонняя музыкальная дорожка" in conclusion
    assert "На таймкоде 01:05:15:21" not in conclusion
    assert "На таймкоде 01:08:54:12" not in conclusion
    assert "На таймкоде 01:25:00:20" not in conclusion


def test_generate_subjective_conclusion_uses_python_grouping_when_ai_disabled():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue("01:30:13:17", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, False, False, False, False, True, False),
        Issue("01:33:07:02", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, False, False, False, False, True, False),
        Issue("01:34:27:00", "", "Музыка задвоилась. В предыдущей версии такого не было", False, False, False, False, False, True, False),
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "[ЗАПОЛНИТЬ ВРУЧНУЮ]" not in conclusion
    assert "В нескольких фрагментах в музыке слышится задвоение или параллельная посторонняя музыкальная дорожка" in conclusion
    assert "На таймкоде 01:30:13:17" not in conclusion


def test_main_blocker_music_markers_are_merged_into_general_group():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue("01:05:15:21", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, True, False, False, False, True, False),
        Issue("01:07:35:17", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, True, False, False, False, True, False),
        Issue("01:20:22:05", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", False, True, False, False, False, True, False),
        Issue("01:30:13:17", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", True, False, False, False, False, True, False),
        Issue("01:33:07:02", "", "В музыке такое впечатление, что паралельно звучит еще какая-то музыка. Грязь. Такого не было в предыдущей версии", True, False, False, False, False, True, False),
        Issue("01:34:27:00", "", "Музыка задвоилась. В предыдущей версии такого не было", True, False, False, False, False, True, False),
        Issue("01:08:58:11", "", "В данном фрагменте видно, что анимационный персонаж справа что-то произносит в ответ на вопросительную реплику актрисы, но его реплика не звучит. Просьба подтвердить, если это является творческим решением", True, False, False, False, False, True, False),
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "На таймкоде 01:08:58:11" in conclusion
    assert "В нескольких фрагментах в музыке слышится задвоение или параллельная посторонняя музыкальная дорожка" in conclusion
    assert "На таймкоде 01:30:13:17" not in conclusion
    assert "На таймкоде 01:33:07:02" not in conclusion
    assert "На таймкоде 01:34:27:00" not in conclusion


def test_me_conclusion_keeps_single_sync_marker():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "00:10:15:12",
            "",
            "Обе дорожки не синхронны с изображением на три кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "00:10:15:12" in conclusion
    assert "обе дорожки не синхронны с изображением на три кадра вправо" in conclusion.lower()


def test_me_voice_markers_are_split_into_gurs_and_optional_track_groups():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:04:52:23",
            "",
            'В данном фрагменте присутствует разборчивая реплика "Да ладно" в гур-гуре',
            True,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:12:25:20",
            "",
            "В данном фрагменте присутствуют реплики на русском языке, которые раздаются из колонок при воспроизведении фильма. Стоит вывести их в отдельный опциональный трек и убрать из M&E микса",
            True,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "реплики_в_гурах" in groups
    assert "опциональный_трек" in groups
    assert "проблемы_реплик" not in groups


def test_me_conclusion_does_not_merge_gur_replicas_into_generic_actor_lines():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:04:52:23",
            "",
            'В данном фрагменте присутствует разборчивая реплика "Да ладно" в гур-гуре',
            True,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:12:25:20",
            "",
            "В данном фрагменте присутствуют реплики на русском языке, которые раздаются из колонок при воспроизведении фильма. Стоит вывести их в отдельный опциональный трек и убрать из M&E микса",
            True,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:18:10:00",
            "",
            "Присутствует реплика актёра",
            True,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "me")

    assert 'На таймкоде 01:04:52:23 присутствует разборчивая реплика "Да ладно" в гур-гуре' in conclusion
    assert "На таймкоде 01:12:25:20 присутствуют реплики на русском языке, которые раздаются из колонок при воспроизведении фильма" in conclusion
    assert "На таймкоде 01:18:10:00 присутствуют реплики актёров" in conclusion
    assert "На таймкодах 01:04:52:23 и 01:18:10:00 присутствуют реплики актёров" not in conclusion


def test_me_voice_marker_without_gurs_stays_in_generic_actor_replicas_group():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:18:10:00",
            "",
            "Присутствует реплика актёра",
            True,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "проблемы_реплик" in groups


def test_me_typo_absent_handshake_is_grouped_as_sync_noise_issue():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:10:00:00",
            "",
            "Отсутсвует звук рукопожатия",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:10:05:00",
            "",
            "Отсутствует звук удара ладонью",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "отсутствие_звука__sync" in groups
    assert "другие_проблемы" not in groups


def test_me_sound_design_markers_are_grouped_separately_from_sync_noises():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:02:13:11",
            "",
            "В данном фрагменте звуки ударов пальцами по столу сильно отличается от оригинальной звуковой дорожки",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:23:17:19",
            "",
            "Звуки взаимодействия с листами бумаги намного ярче в оригинальной звуковой дорожке",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "саунд_дизайн__sync" in groups
    assert "отсутствие_звука" not in groups
    assert "другие_проблемы" not in groups


def test_me_absence_groups_split_into_sync_background_and_music():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:00:01:00",
            "",
            "В данном фрагменте не хватает синхронных шумов (дверца шкафа, перелистывание бумаги, шаги)",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:00:02:00",
            "",
            "В данном фрагменте отсутствует фоновый шум из салона автомобиля в движении",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:00:03:00",
            "",
            "В данном фрагменте отсутствует музыка",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "отсутствие_звука__sync" in groups
    assert "отсутствие_звука__background" in groups
    assert "отсутствие_звука__music" in groups


def test_me_object_sound_absence_defaults_to_sync_domain():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:01:30:22",
            "",
            "Отсутствует звук соприкосновения бокалов",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:03:08:03",
            "",
            "Не хватает звука взаимодействия с сумкой и движения актёра",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:07:45:19",
            "",
            "В данном фрагменте отсутствуют звуки объятий актрис",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "отсутствие_звука__sync" in groups
    assert "отсутствие_звука" not in groups


def test_me_generic_sync_absence_marker_is_suppressed_when_specific_markers_exist():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:00:10:00",
            "",
            "В нескольких фрагментах отсутствуют синхронные шумы",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:12:05:11",
            "",
            "В данном фрагменте отсутствует звук задеваемых актрисой серёжек, когда она подносит телефон к уху",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:20:27:14",
            "",
            "Отсутстствует звук складывания рук",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._normalize_me_groups_for_conclusion(
        generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    )
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "отсутствие_звука__sync" in groups
    assert len(groups["отсутствие_звука__sync"]) == 2
    assert "отсутствие_звука__background" not in groups
    assert "атмосфера" not in groups
    assert "В нескольких фрагментах отсутствуют синхронные шумы" not in conclusion
    assert "На таймкодах 01:12:05:11 и 01:20:27:14 отсутствуют синхронные шумы" in conclusion


def test_me_sound_design_groups_split_into_sync_background_and_music():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:00:01:00",
            "",
            "В данном фрагменте синхронные шумы сильно отличаются по звучанию от оригинальной звуковой дорожки",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:00:02:00",
            "",
            "В данном фрагменте фоновый шум из салона автомобиля отличается от оригинальной звуковой дорожки",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:00:03:00",
            "",
            "В данном фрагменте звучание фоновой музыки отличается от оригинальной звуковой дорожки. Ощущение, что срезались верхние частоты",
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "саунд_дизайн__sync" in groups
    assert "саунд_дизайн__background" in groups
    assert "саунд_дизайн__music" in groups


def test_me_generalized_sound_design_summary_uses_dedicated_group_text():
    generator = ConclusionGenerator()
    groups = {
        "саунд_дизайн": [
            Issue(
                "01:02:13:11",
                "",
                "В данном фрагменте звуки ударов пальцами по столу сильно отличается от оригинальной звуковой дорожки",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:21:20:15",
                "",
                "Звук закрывания двери отличается от оригинальной звуковой дорожки",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:23:17:19",
                "",
                "Звуки взаимодействия с листами бумаги намного ярче в оригинальной звуковой дорожке",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
        ]
    }

    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "В нескольких фрагментах саунд-дизайн отличается от оригинальной звуковой дорожки" in conclusion


def test_me_generalized_domain_specific_absence_and_sound_design_have_separate_texts():
    generator = ConclusionGenerator()

    absence_conclusion = generator._python_format_conclusion(
        [],
        {
            "отсутствие_звука__background": [
                Issue("01:00:01:00", "", "В данном фрагменте отсутствует фоновый шум", False, False, False, False, False, True, False),
                Issue("01:00:02:00", "", "В данном фрагменте отсутствует фоновый шум", False, False, False, False, False, True, False),
                Issue("01:00:03:00", "", "В данном фрагменте отсутствует фоновый шум", False, False, False, False, False, True, False),
            ],
            "отсутствие_звука__music": [
                Issue("01:01:01:00", "", "В данном фрагменте отсутствует музыка", False, False, False, False, False, True, False),
                Issue("01:01:02:00", "", "В данном фрагменте отсутствует музыка", False, False, False, False, False, True, False),
                Issue("01:01:03:00", "", "В данном фрагменте отсутствует музыка", False, False, False, False, False, True, False),
            ],
        },
        "me",
    )

    sound_design_conclusion = generator._python_format_conclusion(
        [],
        {
            "саунд_дизайн__sync": [
                Issue("01:02:01:00", "", "В данном фрагменте синхронные шумы сильно отличаются по звучанию от оригинальной звуковой дорожки", False, False, False, False, False, True, False),
                Issue("01:02:02:00", "", "В данном фрагменте синхронные шумы сильно отличаются по звучанию от оригинальной звуковой дорожки", False, False, False, False, False, True, False),
                Issue("01:02:03:00", "", "В данном фрагменте синхронные шумы сильно отличаются по звучанию от оригинальной звуковой дорожки", False, False, False, False, False, True, False),
            ],
            "саунд_дизайн__music": [
                Issue("01:03:01:00", "", "В данном фрагменте звучание фоновой музыки отличается от оригинальной звуковой дорожки", False, False, False, False, False, True, False),
                Issue("01:03:02:00", "", "В данном фрагменте звучание фоновой музыки отличается от оригинальной звуковой дорожки", False, False, False, False, False, True, False),
                Issue("01:03:03:00", "", "В данном фрагменте звучание фоновой музыки отличается от оригинальной звуковой дорожки", False, False, False, False, False, True, False),
            ],
        },
        "me",
    )

    assert "В нескольких фрагментах отсутствуют фоновые шумы" in absence_conclusion
    assert "В нескольких фрагментах отсутствует музыка" in absence_conclusion
    assert "В нескольких фрагментах синхронные шумы отличаются от оригинальной звуковой дорожки" in sound_design_conclusion
    assert "В нескольких фрагментах музыка отличается от оригинальной звуковой дорожки" in sound_design_conclusion


def test_me_global_sync_marker_at_zero_timecode_is_written_without_tc():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "00:00:00:00",
            "",
            "Обе дорожки не синхронны с изображением на три кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "обе дорожки не синхронны с изображением на три кадра вправо" in conclusion.lower()
    assert "00:00:00:00" not in conclusion
    assert "На таймкоде" not in conclusion


def test_me_global_sync_marker_at_one_hour_is_also_written_without_tc():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:00:00:00",
            "",
            "Обе дорожки не синхронны с изображением на 3 кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "Обе дорожки не синхронны с изображением на 3 кадра вправо" in conclusion
    assert "01:00:00:00" not in conclusion
    assert "На таймкоде" not in conclusion


def test_me_conclusion_preserves_unique_sync_marker_inside_group():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "00:00:00:00",
            "",
            "Обе дорожки не синхронны с изображением на три кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "00:21:05:00",
            "",
            "Звуковые дорожки не синхронны с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "00:35:44:08",
            "",
            "Звуковые дорожки не синхронны с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "-    Обе дорожки не синхронны с изображением на три кадра вправо" in conclusion
    assert "00:00:00:00" not in conclusion
    assert "На таймкодах 00:21:05:00 и 00:35:44:08 звуковые дорожки не синхронны с изображением" in conclusion


def test_me_global_sync_issue_suppresses_local_sync_duplicates_in_conclusion():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:00:00:00",
            "",
            "Обе дорожки не синхронны с изображением на 3 кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:07:17:20",
            "",
            "Звук как актер проводит рукой по лицу, выглядит несинхронно с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:24:46:07",
            "",
            "Шаги актера выглядят несинхронно с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._normalize_me_groups_for_conclusion(
        generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    )
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "-    Обе дорожки не синхронны с изображением на 3 кадра вправо" in conclusion
    assert "На таймкодах 01:07:17:20 и 01:24:46:07 отсутствуют синхронные шумы" in conclusion
    assert "-    Обе дорожки не синхронны с изображением на 3 кадра вправо" in conclusion


def test_main_global_sync_marker_at_zero_timecode_is_written_without_tc():
    generator = ConclusionGenerator()
    groups = {
        'несинхронность': [
            Issue(
                "00:00:00:00",
                "",
                "Звуковые дорожки не синхронны с изображением на 8 кадров",
                True,
                True,
                False,
                False,
                False,
                True,
                False,
            ),
        ],
    }

    conclusion = generator._python_format_conclusion([], groups, "main")

    assert "Звуковые дорожки не синхронны с изображением на 8 кадров" in conclusion
    assert "00:00:00:00" not in conclusion
    assert "На таймкоде" not in conclusion


def test_main_zero_timecode_global_issue_is_split_from_local_markers():
    generator = ConclusionGenerator()
    groups = {
        'отсутствие_звука': [
            Issue(
                "01:00:00:00",
                "",
                "Видеофайл без звука",
                True,
                True,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:12:29:05",
                "",
                "Отсутствует звук",
                True,
                True,
                False,
                False,
                False,
                True,
                False,
            ),
        ],
    }

    conclusion = generator._python_format_conclusion([], groups, "main")

    assert "-    Видеофайл без звука" in conclusion
    assert "-    На таймкоде 01:12:29:05 отсутствует звук" in conclusion
    assert "На таймкодах 01:00:00:00 и 01:12:29:05" not in conclusion
    assert "На таймкоде 01:00:00:00" not in conclusion


def test_me_action_sync_markers_are_treated_as_missing_sync_noises():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:07:17:20",
            "",
            "Звук как актер проводит рукой по лицу, выглядит несинхронно с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:24:46:07",
            "",
            "Шаги актера выглядят несинхронно с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._smart_group_issues(issues, "me")

    assert "отсутствие_звука__sync" in groups
    assert "несинхронность" not in groups


def test_me_conclusion_uses_missing_sync_noises_for_action_sync_markers():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "01:00:00:00",
            "",
            "Обе дорожки не синхронны с изображением на 3 кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:07:17:20",
            "",
            "Звук как актер проводит рукой по лицу, выглядит несинхронно с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:24:46:07",
            "",
            "Шаги актера выглядят несинхронно с изображением",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._normalize_me_groups_for_conclusion(
        generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    )
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "-    Обе дорожки не синхронны с изображением на 3 кадра вправо" in conclusion
    assert "На таймкодах 01:07:17:20 и 01:24:46:07 отсутствуют синхронные шумы" in conclusion
    assert "выглядят несинхронно с изображением" not in conclusion


def test_me_conclusion_keeps_generalization_for_multiple_unique_sync_markers():
    generator = ConclusionGenerator()
    issues = [
        Issue(
            "00:10:15:12",
            "",
            "Обе дорожки не синхронны с изображением на три кадра вправо",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "00:21:05:00",
            "",
            "Звуковые дорожки отстают от изображения",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "00:35:44:08",
            "",
            "Звуковые дорожки опережают изображение",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    groups = generator._merge_me_context_groups(generator._smart_group_issues(issues, "me"))
    conclusion = generator._python_format_conclusion([], groups, "me")

    assert "В нескольких фрагментах" in conclusion
    assert "звуковые дорожки несинхронны с изображением" in conclusion
    assert "00:10:15:12" not in conclusion
    assert "00:21:05:00" not in conclusion
    assert "00:35:44:08" not in conclusion
    assert "на три кадра вправо" not in conclusion


def test_validate_polished_rejects_missing_required_timecode():
    generator = ConclusionGenerator()
    original = (
        "По субъективной оценке выявлены следующие недочёты:\n\n"
        "-    На таймкоде 00:10:15:12 обе дорожки не синхронны с изображением на три кадра вправо"
    )
    polished = (
        "По субъективной оценке выявлены следующие недочёты:\n\n"
        "-    В нескольких фрагментах звуковые дорожки несинхронны с изображением"
    )

    assert generator._validate_polished(original, polished, "me") is False


def test_validate_polished_rejects_extra_specific_lines_for_generalized_group():
    generator = ConclusionGenerator()
    original = (
        "По субъективной оценке выявлены следующие недочёты:\n\n"
        "-    В нескольких фрагментах в музыке слышится задвоение или параллельная посторонняя музыкальная дорожка"
    )
    polished = (
        "По субъективной оценке выявлены следующие недочёты:\n\n"
        "-    На таймкоде 01:30:13:17 в музыке такое впечатление, что параллельно звучит еще какая-то музыка. Грязь\n"
        "-    На таймкоде 01:33:07:02 в музыке такое впечатление, что параллельно звучит еще какая-то музыка. Грязь\n"
        "-    В нескольких фрагментах в музыке слышится задвоение или параллельная посторонняя музыкальная дорожка"
    )

    assert generator._validate_polished(original, polished, "main") is False


def test_all_reports_use_blockers_then_specific_then_general_ordering():
    generator = ConclusionGenerator()
    groups = {
        "другие_проблемы": [
            Issue(
                "01:12:29:05",
                "",
                "Слышна склейка фонового шума моря",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            )
        ],
        "щелчки_слюна": [
            Issue(
                "01:20:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:21:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:22:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
            Issue(
                "01:23:00:00",
                "",
                "Слышны щёлкающие звуки на репликах",
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            ),
        ],
    }
    blockers = [
        Issue(
            "01:00:49:06",
            "",
            "Видео не синхронно со звуковыми дорожками",
            True,
            True,
            False,
            False,
            False,
            True,
            False,
        )
    ]

    conclusion = generator._python_format_conclusion(blockers, groups, "main")
    lines = [line for line in conclusion.splitlines() if line.startswith("-    ")]

    assert lines == [
        "-    На таймкоде 01:00:49:06 видео не синхронно со звуковыми дорожками",
        "-    На таймкоде 01:12:29:05 слышна склейка фонового шума моря",
        "-    В фонограмме присутствуют посторонние щёлкающие звуки",
    ]


def test_subjective_conclusion_translates_english_click_marker_to_russian():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:03:30:19",
            "",
            "Click on the voice",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        )
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "На таймкоде 01:03:30:19 слышны посторонние щёлкающие звуки" in conclusion


def test_subjective_conclusion_groups_english_channel_markers_like_manual_reports():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:00:41:00",
            "01:01:42:05",
            "The sound is only in the central channel",
            False,
            False,
            True,
            False,
            False,
            False,
            True,
        ),
        Issue(
            "01:03:08:14",
            "01:03:52:12",
            "The sound is only in the central channel",
            False,
            False,
            True,
            False,
            False,
            False,
            True,
        ),
        Issue(
            "01:07:54:14",
            "01:08:32:09",
            "The sound dissappeared in left and right surround channels in this fragment",
            False,
            False,
            True,
            False,
            False,
            False,
            True,
        ),
        Issue(
            "01:09:29:09",
            "01:10:09:19",
            "The sound dissappeared in left and right surround channels in this fragment",
            False,
            False,
            True,
            False,
            False,
            False,
            True,
        ),
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "В звуковой дорожке 5.1 есть фрагменты, в которых присутствует звуковой сигнал только в центральном канале" in conclusion
    assert "В звуковой дорожке 5.1 есть сцены, в которых отсутствует звуковой сигнал в каналах Ls и Rs" in conclusion


def test_prepare_issues_uses_reference_style_rules_for_english_noise_and_screensaver():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:00:00:00",
            "01:00:04:04",
            "The Yandex screensaver sounds without a central channel. Differs from the original version",
            False,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:24:36:08",
            "01:24:40:01",
            "White background noise on the woman's speech",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    prepared = generator._prepare_issues_for_subjective_conclusion(issues)

    assert prepared[0].description_ru == 'заставка "Yandex" воспроизводится без центрального канала'
    assert prepared[1].description_ru == "на реплике слышен фоновый белый шум"


def test_prepare_issues_detects_whistling_and_high_frequency_speech_markers():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:10:30:01",
            "",
            "Whistle sound have excessive high frequency level on the woman's speech",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:29:57:24",
            "",
            "High frequency sound before the actor's line",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
    ]

    prepared = generator._prepare_issues_for_subjective_conclusion(issues)
    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert prepared[0].description_ru == "на реплике слышен свистящий высокочастотный призвук"
    assert prepared[1].description_ru == "перед репликой слышен посторонний высокочастотный звук"
    assert "высокочастот" in conclusion.lower() or "свист" in conclusion.lower()


def test_prepare_issues_detects_exact_s_sound_marker_from_english_marker_list():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:13:35:15",
            "",
            '"S" sound have excessive high frequency level',
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        )
    ]

    prepared = generator._prepare_issues_for_subjective_conclusion(issues)
    conclusion = generator.generate_subjective_conclusion(
        issues * 4,
        "main",
    )

    assert prepared[0].description_ru == "на реплике слышно яркое свистящее звучание звука «С»"
    assert "На некоторых репликах слышно яркое свистящее звучание звука «С»" in conclusion


def test_prepare_issues_prefers_russian_comment_translation_when_present():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:06:27:01",
            "01:07:35:14",
            "The music on the screensaver plays only in stereo",
            False,
            False,
            True,
            False,
            False,
            False,
            True,
            comments="музыка на заставке играет только в стерео",
        )
    ]

    prepared = generator._prepare_issues_for_subjective_conclusion(issues)
    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert prepared[0].description_ru == "музыка на заставке играет только в стерео"
    assert "музыка на заставке играет только в стерео" in conclusion.lower()


def test_prepare_issues_translates_unsync_and_credits_stereo_markers():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:16:03:23",
            "01:16:04:20",
            "Man's phrase looks little unsync",
            True,
            False,
            True,
            False,
            False,
            True,
            False,
        ),
        Issue(
            "01:36:46:16",
            "01:39:30:14",
            "The music in the credits is played only in stereo format",
            False,
            False,
            True,
            False,
            False,
            False,
            True,
        ),
    ]

    prepared = generator._prepare_issues_for_subjective_conclusion(issues)
    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert prepared[0].description_ru == "реплика выглядит немного несинхронной"
    assert prepared[1].description_ru == "музыка на титрах воспроизводится только в формате стерео"
    assert "реплика выглядит немного несинхронной" in conclusion.lower()
    assert "музыка на титрах воспроизводится только в формате стерео" in conclusion.lower()


def test_question_marker_with_factual_part_keeps_only_statement():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:02:24:05",
            "",
            "We hear the cut after the music. Can we add fade out please?",
            False,
            False,
            True,
            False,
            False,
            True,
            False,
        )
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "склейка" in conclusion.lower()
    assert "fade" not in conclusion.lower()


def test_pure_intent_question_marker_is_dropped():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:10:00:00",
            "",
            "комментаторы не озвучены, так задуманно?",
            False,
            False,
            True,
            False,
            False,
            True,
            False,
        )
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "нареканий не обнаружено" in conclusion
    assert "комментаторы" not in conclusion.lower()


def test_tentative_question_marker_kept_as_statement():
    generator = ConclusionGenerator(use_llm=False)
    issues = [
        Issue(
            "01:00:15:20",
            "",
            "Missing phrase?",
            False,
            False,
            True,
            False,
            False,
            True,
            False,
        )
    ]

    conclusion = generator.generate_subjective_conclusion(issues, "main")

    assert "отсутствует реплика" in conclusion.lower()
    assert "?" not in conclusion
