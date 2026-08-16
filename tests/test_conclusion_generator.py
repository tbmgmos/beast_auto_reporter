import pytest

from src.conclusion_generator import ConclusionGenerator, _extract_anchor_tokens
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


def test_num_ctx_defaults_to_8192():
    assert ConclusionGenerator().num_ctx == 8192


def test_num_ctx_read_from_config():
    generator = ConclusionGenerator(config={"llm": {"num_ctx": 4096}})

    assert generator.num_ctx == 4096


def test_llm_provider_defaults_to_ollama():
    assert ConclusionGenerator().llm_provider == "ollama"


def test_llm_provider_read_from_config():
    generator = ConclusionGenerator(config={"llm": {"provider": "groq"}})

    assert generator.llm_provider == "groq"


def test_set_llm_provider_switches_at_runtime():
    generator = ConclusionGenerator()
    generator.set_llm_provider("groq")

    assert generator.llm_provider == "groq"


def test_ollama_generate_dispatches_to_ollama_by_default():
    generator = ConclusionGenerator()
    generator.ollama_service.generate = lambda prompt, **kw: "из ollama"
    generator.groq_service.generate = lambda prompt, **kw: "из groq"

    assert generator._ollama_generate("промпт") == "из ollama"


def test_ollama_generate_dispatches_to_groq_when_provider_switched():
    generator = ConclusionGenerator()
    generator.set_llm_provider("groq")
    generator.ollama_service.generate = lambda prompt, **kw: "из ollama"
    generator.groq_service.generate = lambda prompt, **kw: "из groq"

    assert generator._ollama_generate("промпт") == "из groq"


def test_ollama_generate_dispatches_to_yandexgpt_when_provider_switched():
    generator = ConclusionGenerator()
    generator.set_llm_provider("yandexgpt")
    generator.ollama_service.generate = lambda prompt, **kw: "из ollama"
    generator.yandexgpt_service.generate = lambda prompt, **kw: "из yandexgpt"

    assert generator._ollama_generate("промпт") == "из yandexgpt"


def test_ollama_generate_dispatches_to_gigachat_when_provider_switched():
    generator = ConclusionGenerator()
    generator.set_llm_provider("gigachat")
    generator.ollama_service.generate = lambda prompt, **kw: "из ollama"
    generator.gigachat_service.generate = lambda prompt, **kw: "из gigachat"

    assert generator._ollama_generate("промпт") == "из gigachat"


def test_marker_translation_uses_active_provider_not_hardcoded_ollama():
    """Перевод маркеров раньше был жёстко привязан к self.ollama_service

    напрямую, в обход диспетчера провайдера — переключение на Groq/YandexGPT
    для заключений не влияло на перевод. Теперь MarkerTranslationService
    получает _ollama_generate и должен использовать текущий провайдер.
    """
    generator = ConclusionGenerator()
    generator.set_llm_provider("groq")
    generator.ollama_service.generate = lambda prompt, **kw: '[{"index": 0, "translation": "из ollama"}]'
    generator.groq_service.generate = lambda prompt, **kw: '[{"index": 0, "translation": "из groq"}]'

    result = generator.marker_translation_service._translate_batch_with_llm(["Click on the voice"])

    assert result["Click on the voice"] == "из groq"


def test_extract_anchor_tokens_finds_quotes_and_numeric_units():
    text = 'Окончание фамилии "Ходяков" звучит обрезано, смещение ~8 кадров'

    anchors = _extract_anchor_tokens(text)

    assert "ходяков" in anchors
    assert "~8 кадров" in anchors


def test_extract_anchor_tokens_empty_for_text_without_anchors():
    assert _extract_anchor_tokens("Реплики выглядят несинхронно с изображением") == set()
    assert _extract_anchor_tokens("") == set()


def _single_item_issue(timecode="01:18:49:24", desc='Окончание фамилии "Ходяков" звучит обрезано'):
    return Issue(timecode, "", desc, False, False, False, False, False, True, False)


def _issue(timecode, desc, blocker=False):
    return Issue(timecode, "", desc, False, False, False, False, blocker, not blocker, False)


def test_count_distinct_issue_types_counts_unique_group_types():
    generator = ConclusionGenerator()
    issues = [
        _issue("01:00:00:00", "Слышны щёлкающие звуки"),
        _issue("01:00:05:00", "Слышны щёлкающие звуки"),
        _issue("01:00:10:00", "Реплика несинхронна с изображением"),
    ]

    distinct = generator.count_distinct_issue_types(issues)

    assert distinct == len({generator._classify_single_issue(i) for i in issues})
    assert distinct <= len(issues)


def test_maybe_auto_select_provider_returns_none_below_thresholds():
    generator = ConclusionGenerator()
    generator.auto_select_marker_threshold = 100
    generator.auto_select_distinct_types_threshold = 100
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider is None
    assert cloud_unavailable is False


def test_maybe_auto_select_provider_does_nothing_if_already_on_cloud():
    generator = ConclusionGenerator()
    generator.set_llm_provider("groq")
    generator.auto_select_marker_threshold = 1
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider is None
    assert cloud_unavailable is False


def test_maybe_auto_select_provider_switches_on_marker_count_threshold():
    generator = ConclusionGenerator()
    generator.use_llm = True
    generator.auto_select_marker_threshold = 2
    generator.auto_select_distinct_types_threshold = 100
    generator.gigachat_service.get_auth_key = lambda: "fake-key"
    generator.gigachat_service.check_status = lambda: True
    issues = [
        _issue("01:00:00:00", "Слышны щёлкающие звуки"),
        _issue("01:00:05:00", "Слышны щёлкающие звуки"),
    ]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider == "gigachat"
    assert cloud_unavailable is False


def test_maybe_auto_select_provider_respects_priority_order():
    """GigaChat (бесплатно, без VPN) должен предпочитаться YandexGPT/Groq,

    даже если все три настроены."""
    generator = ConclusionGenerator()
    generator.use_llm = True
    generator.auto_select_marker_threshold = 1
    generator.gigachat_service.get_auth_key = lambda: "fake-key"
    generator.gigachat_service.check_status = lambda: True
    generator.yandexgpt_service.get_api_key = lambda: "fake-key"
    generator.yandexgpt_service.get_folder_id = lambda: "fake-folder"
    generator.yandexgpt_service.check_status = lambda: True
    generator.groq_service.get_api_key = lambda: "fake-key"
    generator.groq_service.check_status = lambda: True
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, _ = generator.maybe_auto_select_provider(issues)

    assert provider == "gigachat"


def test_maybe_auto_select_provider_falls_through_to_next_when_unreachable():
    generator = ConclusionGenerator()
    generator.use_llm = True
    generator.auto_select_marker_threshold = 1
    generator.gigachat_service.get_auth_key = lambda: "fake-key"
    generator.gigachat_service.check_status = lambda: False  # настроен, но недоступен
    generator.yandexgpt_service.get_api_key = lambda: "fake-key"
    generator.yandexgpt_service.get_folder_id = lambda: "fake-folder"
    generator.yandexgpt_service.check_status = lambda: True
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider == "yandexgpt"
    assert cloud_unavailable is False


def test_maybe_auto_select_provider_reports_unavailable_when_nothing_reachable():
    generator = ConclusionGenerator()
    generator.use_llm = True
    generator.auto_select_marker_threshold = 1
    # Реальная Связка ключей на машине разработчика может реально содержать
    # ключи Groq/YandexGPT (использовались для живого тестирования сервисов
    # в этой же сессии) — явно обнуляем, чтобы тест не зависел от состояния
    # окружения.
    generator.groq_service.get_api_key = lambda: ""
    generator.yandexgpt_service.get_api_key = lambda: ""
    generator.yandexgpt_service.get_folder_id = lambda: ""
    generator.gigachat_service.get_auth_key = lambda: "fake-key"
    generator.gigachat_service.check_status = lambda: False
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider is None
    assert cloud_unavailable is True


def test_maybe_auto_select_provider_stays_local_when_nothing_configured():
    generator = ConclusionGenerator()
    generator.use_llm = True
    generator.auto_select_marker_threshold = 1
    generator.gigachat_service.get_auth_key = lambda: ""
    generator.yandexgpt_service.get_api_key = lambda: ""
    generator.yandexgpt_service.get_folder_id = lambda: ""
    generator.groq_service.get_api_key = lambda: ""
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider is None
    assert cloud_unavailable is False


def test_auto_select_llm_enabled_by_default():
    assert ConclusionGenerator().auto_select_llm_enabled is True


def test_auto_select_llm_enabled_read_from_config():
    generator = ConclusionGenerator(config={"llm": {"auto_select_enabled": False}})
    assert generator.auto_select_llm_enabled is False


def test_set_auto_select_llm_enabled_switches_at_runtime():
    generator = ConclusionGenerator()
    generator.set_auto_select_llm_enabled(False)
    assert generator.auto_select_llm_enabled is False


def test_maybe_auto_select_provider_returns_none_when_disabled():
    generator = ConclusionGenerator()
    generator.use_llm = True
    generator.auto_select_marker_threshold = 1
    generator.auto_select_llm_enabled = False
    generator.gigachat_service.get_auth_key = lambda: "fake-key"
    generator.gigachat_service.check_status = lambda: True
    issues = [_issue("01:00:00:00", "Слышны щёлкающие звуки")]

    provider, cloud_unavailable = generator.maybe_auto_select_provider(issues)

    assert provider is None
    assert cloud_unavailable is False


def test_format_multiple_issue_shipenie_bare_type_is_not_generic():
    """Регрессия: для report_type="main" и group_type="шипение" без подтипа

    (__s_sound/__whistle/__high_freq) _format_multiple_issue проваливался в
    generic "присутствуют проблемы", потому что ветка была только внутри
    `if is_me:`. Найдено реальным прогоном ulichnaya_eda CSV через GigaChat —
    LLM-ответ отклонён anchor-проверкой, и обнажился пустой python-фолбэк.
    """
    generator = ConclusionGenerator()
    issues = [
        _issue("01:17:59:11", 'Постороннее шипение на реплике "Не парься ..."'),
        _issue("01:37:47:14", 'Постороннее шипение на реплике "Ты мне нравишься очень"'),
    ]

    result = generator._format_multiple_issue("шипение", issues, report_type="main")

    assert result != "присутствуют проблемы"
    assert "шипени" in result.lower()


def test_format_multiple_issue_shchelchki_slyuna_bare_type_is_not_generic():
    """Тот же класс бага, что и с 'шипение' — щелчки_слюна тоже не была

    обработана в _format_multiple_issue вне is_me (была только в
    _format_generalized_issue для групп 4+)."""
    generator = ConclusionGenerator()
    issues = [
        _issue("01:00:00:00", "Посторонний щёлкающий звук в левом канале"),
        _issue("01:01:00:00", "Яркий звук слюны перед репликой"),
    ]

    result = generator._format_multiple_issue("щелчки_слюна", issues, report_type="main")

    assert result != "присутствуют проблемы"
    assert "щёлкающ" in result.lower() or "слюн" in result.lower()


def test_probe_provider_reachable_restores_original_timeout():
    generator = ConclusionGenerator()
    generator.groq_service.timeout = 60
    generator.groq_service.check_status = lambda: True

    assert generator._probe_provider_reachable("groq", timeout=4) is True
    assert generator.groq_service.timeout == 60


def test_summarize_item_with_llm_uses_ollama_response_when_valid():
    generator = ConclusionGenerator()
    issue = _single_item_issue()
    expected_item = generator._build_structured_contract_item(
        "specific", f"На таймкоде {issue.timecode_in} ...", source_issues=[issue],
    )
    generator.ollama_service.generate = lambda prompt, **kw: 'окончание фамилии "Ходяков" звучит совсем обрезано'

    result = generator._summarize_item_with_llm(expected_item, is_me=False)

    assert result == 'окончание фамилии "Ходяков" звучит совсем обрезано'


def test_summarize_item_with_llm_returns_none_when_anchor_lost():
    generator = ConclusionGenerator()
    issue = _single_item_issue()
    expected_item = generator._build_structured_contract_item(
        "specific", f"На таймкоде {issue.timecode_in} ...", source_issues=[issue],
    )
    generator.ollama_service.generate = lambda prompt, **kw: "окончание фамилии звучит обрезано"

    assert generator._summarize_item_with_llm(expected_item, is_me=False) is None


def test_summarize_item_with_llm_accepts_generalized_summary_for_large_group():
    """Регрессия на реальный кейс (GAMES M&E, эта сессия): для группы из 4+

    маркеров с разными цитатами промпт прямо требует "обобщи, не перечисляя
    каждый" — дословное сохранение ВСЕХ цитат в одном обобщающем
    предложении невозможно и не должно требоваться. Раньше это приводило к
    тому, что валидное обобщение отклонялось и заменялось на голый
    python-фолбэк (треть пунктов реального отчёта превращалась в шаблон).
    """
    generator = ConclusionGenerator()
    issues = [
        Issue("01:07:20:23", "", 'звучит фраза "стоять! Кому говорят?"', False, False, False, False, False, True, False),
        Issue("01:07:23:17", "", 'звучит фраза "давай"', False, False, False, False, False, True, False),
        Issue("01:13:19:24", "", 'слышна часть реплики тренера "ааа"', False, False, False, False, False, True, False),
        Issue("01:24:58:09", "", 'слышно слово "хочешь"', False, False, False, False, False, True, False),
    ]
    expected_item = generator._build_structured_contract_item(
        "blocker", "В нескольких фрагментах присутствуют реплики актёров", source_issues=issues,
    )
    # Обобщённая формулировка без единой из 4 цитат — именно то, что и просит
    # правило для 4+ маркеров.
    generator.ollama_service.generate = lambda prompt, **kw: "В нескольких фрагментах присутствуют реплики актёров"

    result = generator._summarize_item_with_llm(expected_item, is_me=True)

    assert result == "В нескольких фрагментах присутствуют реплики актёров"


def test_summarize_item_with_llm_returns_none_on_banned_phrase():
    generator = ConclusionGenerator()
    issue = Issue("01:00:00:00", "", "Слышны щёлкающие звуки", False, False, False, False, False, True, False)
    expected_item = generator._build_structured_contract_item(
        "general", "В нескольких фрагментах ...", source_issues=[issue],
    )
    generator.ollama_service.generate = lambda prompt, **kw: "в целом рекомендуем пересмотреть звук"

    assert generator._summarize_item_with_llm(expected_item, is_me=False) is None


def test_summarize_item_with_llm_returns_none_without_source_issues():
    generator = ConclusionGenerator()
    expected_item = generator._build_structured_contract_item("general", "В нескольких фрагментах ...")

    assert generator._summarize_item_with_llm(expected_item, is_me=False) is None


def test_summarize_item_with_llm_returns_none_on_ollama_exception():
    generator = ConclusionGenerator()
    issue = _single_item_issue()
    expected_item = generator._build_structured_contract_item(
        "specific", f"На таймкоде {issue.timecode_in} ...", source_issues=[issue],
    )

    def _raise(prompt, **kw):
        raise RuntimeError("Ollama недоступна")

    generator.ollama_service.generate = _raise

    assert generator._summarize_item_with_llm(expected_item, is_me=False) is None


def test_build_item_summary_prompt_mentions_count_rule_and_markers():
    generator = ConclusionGenerator()
    issues = [
        Issue("01:00:00:00", "", "Слышны щёлкающие звуки", False, False, False, False, False, True, False),
        Issue("01:01:00:00", "", "Слышны щёлкающие звуки", False, False, False, False, False, True, False),
    ]
    expected_item = generator._build_structured_contract_item(
        "general", "В нескольких фрагментах ...", source_issues=issues,
    )

    prompt = generator._build_item_summary_prompt(expected_item, is_me=False)

    assert "Слышны щёлкающие звуки" in prompt
    assert "2 маркера" in prompt


def test_build_item_summary_prompt_forbids_quoting_replicas_for_large_groups():
    """Регрессия на реальный кейс: для группы 4+ модель перечислила все

    цитаты реплик подряд («И нам пора», «В общем из-за выходок» и т.д.) —
    формально это "обобщение", но по факту список цитат, не саммари.
    Промпт для больших групп должен явно запрещать дословные цитаты.
    """
    generator = ConclusionGenerator()
    issues = [
        Issue(f"01:0{i}:00:00", "", f'Реплика "{i}" несинхронна', False, False, False, False, False, True, False)
        for i in range(4)
    ]
    expected_item = generator._build_structured_contract_item(
        "general", "Несинхронность в нескольких фрагментах ...", source_issues=issues,
    )

    prompt = generator._build_item_summary_prompt(expected_item, is_me=False)

    assert "не приводи дословные цитаты" in prompt.lower()


def test_build_item_summary_prompt_includes_me_domain_hint():
    generator = ConclusionGenerator()
    issue = _single_item_issue(desc="Слышны щёлкающие звуки")
    expected_item = generator._build_structured_contract_item(
        "specific", "На таймкоде ...", source_issues=[issue],
    )

    prompt = generator._build_item_summary_prompt(expected_item, is_me=True)

    assert "M&E" in prompt


def test_write_conclusion_with_llm_uses_ai_text_and_falls_back_per_item():
    """Интеграционный тест чанкинга: для одного пункта LLM отвечает валидно,

    для другого — теряет обязательную деталь. Итоговый текст должен
    содержать AI-формулировку для первого пункта и python-фолбэк для
    второго, а не откатываться на python для ВСЕГО заключения сразу.
    """
    generator = ConclusionGenerator()
    groups = {
        "другие_проблемы": [
            _single_item_issue("01:18:49:24", 'Окончание фамилии "Ходяков" звучит обрезано'),
            _single_item_issue("01:20:00:00", 'Слышно слово "хочешь"'),
        ]
    }

    def fake_generate(prompt, **kw):
        if "Ходяков" in prompt:
            return 'фамилия "Ходяков" обрезается на конце реплики'
        return "деталь потеряна тут"  # не сохраняет якорь "хочешь" в кавычках

    generator.ollama_service.generate = fake_generate

    conclusion = generator._write_conclusion_with_llm([], groups, "main")

    assert 'фамилия "Ходяков" обрезается на конце реплики' in conclusion
    assert 'слово "хочешь"' in conclusion  # python-фолбэк для второго пункта
    assert conclusion.startswith("По субъективной оценке выявлены следующие недочёты:")


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
