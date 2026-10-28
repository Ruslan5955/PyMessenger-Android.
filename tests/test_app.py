import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as messenger_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger_app, "DB_PATH", tmp_path / "test.db")
    messenger_app.init_db()
    messenger_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with messenger_app.app.test_client() as test_client:
        yield test_client


def create_account(client, phone, display_name):
    return client.post(
        "/login",
        data={
            "phone": phone,
            "password": "test-password-123",
            "display_name": display_name,
        },
        follow_redirects=True,
    )


def test_login_creates_saved_chat_and_sends_message(client):
    response = create_account(client, "77001234567", "Тестовый пользователь")
    assert response.status_code == 200
    chats = client.get("/api/chats")
    assert chats.status_code == 200
    chat_list = chats.get_json()
    assert len(chat_list) == 1
    chat_id = chat_list[0]["id"]

    sent = client.post(f"/api/messages/{chat_id}", data={"body": "Привет!"})
    assert sent.status_code == 201
    assert sent.get_json()["body"] == "Привет!"

    received = client.get(f"/api/messages/{chat_id}?after=0")
    assert received.status_code == 200
    assert [message["body"] for message in received.get_json()] == ["Привет!"]


def test_api_requires_login(client):
    response = client.get("/api/chats")
    assert response.status_code == 401
    assert response.get_json()["error"]


def test_direct_chat_and_search(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger_app, "DB_PATH", tmp_path / "direct.db")
    messenger_app.init_db()
    messenger_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    first = messenger_app.app.test_client()
    second = messenger_app.app.test_client()
    create_account(first, "77000000001", "Рома")
    create_account(second, "77000000002", "Катя")

    search = first.get("/api/users/search?q=Катя")
    assert search.status_code == 200
    user = search.get_json()[0]
    created = first.post("/api/chats/direct", json={"user_id": user["id"]})
    assert created.status_code == 201
    chat_id = created.get_json()["chat_id"]

    sent = first.post(f"/api/messages/{chat_id}", data={"body": "Тест личного чата"})
    assert sent.status_code == 201

    second_chats = second.get("/api/chats").get_json()
    assert any(chat["id"] == chat_id for chat in second_chats)
    received = second.get(f"/api/messages/{chat_id}?after=0")
    assert received.status_code == 200
    assert received.get_json()[0]["body"] == "Тест личного чата"


def test_wrong_password_is_rejected(client):
    create_account(client, "77009998877", "Пользователь")
    client.get("/logout")
    response = client.post(
        "/login",
        data={"phone": "77009998877", "password": "wrong-password"},
    )
    assert response.status_code == 401



def test_call_signals_are_delivered_only_to_the_other_user(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger_app, "DB_PATH", tmp_path / "calls.db")
    messenger_app.init_db()
    messenger_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    caller = messenger_app.app.test_client()
    callee = messenger_app.app.test_client()
    create_account(caller, "77000000101", "Звонящий")
    create_account(callee, "77000000102", "Получатель")

    other_user = caller.get("/api/users/search?q=Получатель").get_json()[0]
    response = caller.post("/api/chats/direct", json={"user_id": other_user["id"]})
    assert response.status_code == 201
    chat_id = response.get_json()["chat_id"]

    sent = caller.post(
        f"/api/calls/{chat_id}/signal",
        json={"kind": "offer", "payload": '{"sdp":"test-offer"}'},
    )
    assert sent.status_code == 200
    inbox = callee.get("/api/calls/poll?after=0")
    assert inbox.status_code == 200
    signals = inbox.get_json()["signals"]
    assert len(signals) == 1
    assert signals[0]["kind"] == "offer"
    assert signals[0]["chat_id"] == chat_id
    assert signals[0]["from_name"] == "Звонящий"

    # A user cannot send a call signal to a chat they are not a member of.
    denied = caller.post(
        f"/api/calls/{chat_id + 999}/signal",
        json={"kind": "offer", "payload": "{}"},
    )
    assert denied.status_code == 404


def test_reply_edit_delete_search_and_pinned_messages(client):
    create_account(client, "77001234001", "Функции")
    chat_id = client.get("/api/chats").get_json()[0]["id"]

    sent = client.post(f"/api/messages/{chat_id}", data={"body": "Найди этот текст"})
    assert sent.status_code == 201
    message_id = sent.get_json()["id"]

    reply = client.post(
        f"/api/messages/{chat_id}",
        data={"body": "Ответ готов", "reply_to_id": str(message_id)},
    )
    assert reply.status_code == 201
    reply_id = reply.get_json()["id"]

    history = client.get(f"/api/messages/{chat_id}?after=0").get_json()
    assert history[1]["reply_to_id"] == message_id
    assert history[1]["reply_body"] == "Найди этот текст"

    edited = client.patch(
        f"/api/messages/{chat_id}/{message_id}", json={"body": "Изменённый текст"}
    )
    assert edited.status_code == 200
    search = client.get(f"/api/chats/{chat_id}/search?q=Изменённый")
    assert search.status_code == 200
    assert search.get_json()[0]["id"] == message_id

    pinned = client.post(
        f"/api/chats/{chat_id}/pinned", json={"message_id": reply_id}
    )
    assert pinned.status_code == 200
    assert client.get(f"/api/chats/{chat_id}/pinned").get_json()[0]["id"] == reply_id

    deleted = client.delete(f"/api/messages/{chat_id}/{reply_id}")
    assert deleted.status_code == 200
    history = client.get(f"/api/messages/{chat_id}?after=0").get_json()
    assert history[1]["body"] == "Сообщение удалено"


def test_read_receipt_and_group_management(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger_app, "DB_PATH", tmp_path / "features.db")
    messenger_app.init_db()
    messenger_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    owner = messenger_app.app.test_client()
    member = messenger_app.app.test_client()
    create_account(owner, "77000000901", "Создатель")
    create_account(member, "77000000902", "Участник")

    member_data = owner.get("/api/users/search?q=Участник").get_json()[0]
    created = owner.post(
        "/api/chats/group", json={"title": "Тестовая группа", "user_ids": [member_data["id"]]}
    )
    assert created.status_code == 201
    chat_id = created.get_json()["chat_id"]

    owner.post(f"/api/messages/{chat_id}", data={"body": "Прочитай меня"})
    member_history = member.get(f"/api/messages/{chat_id}?after=0")
    assert member_history.status_code == 200
    receipt = owner.get(f"/api/chats/{chat_id}/read-state")
    assert receipt.status_code == 200
    assert receipt.get_json()["last_read_message_id"] >= 1

    renamed = owner.patch(f"/api/chats/{chat_id}/group", json={"title": "Новое имя"})
    assert renamed.status_code == 200
    assert renamed.get_json()["title"] == "Новое имя"
