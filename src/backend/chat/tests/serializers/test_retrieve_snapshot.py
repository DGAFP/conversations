"""Retrieval must not lose a turn that lands after the conversation was loaded."""

from django.db import transaction

import pytest

from chat.ai_sdk_types import TextUIPart, UIMessage
from chat.factories import ChatConversationFactory
from chat.models import ChatConversation, ChatStreamChunk
from chat.serializers import ChatConversationRetrieveSerializer

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("streaming_first", [False, True])
def test_retrieve_loaded_before_completion_includes_the_landed_answer(streaming_first):
    """Both fields must see the committed answer when its chunks are already gone."""
    question = UIMessage(
        id="question", role="user", parts=[TextUIPart(type="text", text="Question RH")]
    )
    answer = UIMessage(
        id="answer",
        role="assistant",
        parts=[TextUIPart(type="text", text="Réponse RH")],
        metadata={
            "assistant_rh": {
                "status": "available",
                "sources": [{"title": "Guide RH", "url": "/protected-source/"}],
            }
        },
    )
    conversation = ChatConversationFactory(messages=[question])
    ChatStreamChunk.objects.create(conversation=conversation, seq=0, text="Réponse partielle")
    loaded_before_completion = ChatConversation.objects.get(pk=conversation.pk)

    with transaction.atomic():
        conversation.messages = [question, answer]
        conversation.save(update_fields=["messages"])
        conversation.stream_chunks.all().delete()

    serializer = ChatConversationRetrieveSerializer(loaded_before_completion)
    if streaming_first:
        assert serializer.get_is_streaming(loaded_before_completion) is False
    data = serializer.data

    assert [message["id"] for message in data["messages"]] == ["question", "answer"]
    assert data["messages"][-1]["metadata"] == answer.metadata
    assert data["is_streaming"] is False
