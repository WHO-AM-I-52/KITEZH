from services.investmap_rf_monitor_queries import (
    _MISSING,
    _collect_payload_changes,
    _history_field_presentation,
)


def test_history_field_presentation_localizes_utility_field():
    assert _history_field_presentation(
        "/powerSupply/availability"
    ) == {
        "section": "Инженерная инфраструктура",
        "label": "Электроснабжение: доступность",
    }


def test_history_field_presentation_localizes_coordinates():
    assert _history_field_presentation(
        "/coordinate/latitude"
    ) == {
        "section": "Расположение",
        "label": "Координаты: широта",
    }


def test_history_field_presentation_hides_unknown_technical_name():
    assert _history_field_presentation(
        "/newApiField/value"
    ) == {
        "section": "Дополнительные сведения",
        "label": "Дополнительное поле API",
    }


def test_collect_payload_changes_reports_nested_scalar_change():
    changes = _collect_payload_changes(
        {
            "powerSupply": {
                "availability": "Возможно подключение",
            },
        },
        {
            "powerSupply": {
                "availability": "Подключено",
            },
        },
    )

    assert len(changes) == 1
    assert changes[0]["field_path"] == "/powerSupply/availability"
    assert changes[0]["label"] == "Электроснабжение: доступность"
    assert changes[0]["old_value"] == "Возможно подключение"
    assert changes[0]["new_value"] == "Подключено"
    assert changes[0]["change_type"] == "changed"


def test_collect_payload_changes_reports_added_and_removed_values():
    added = _collect_payload_changes(
        {},
        {"descriptionApplicationProcedure": "Подать заявку онлайн"},
    )
    removed = _collect_payload_changes(
        {"descriptionApplicationProcedure": "Подать заявку онлайн"},
        {},
    )

    assert added[0]["change_type"] == "added"
    assert added[0]["old_value"] is None
    assert added[0]["new_value"] == "Подать заявку онлайн"

    assert removed[0]["change_type"] == "removed"
    assert removed[0]["old_value"] == "Подать заявку онлайн"
    assert removed[0]["new_value"] is None
