"""Unit tests for project-level LLM instructions injection."""

import pytest

from chat.clients.pydantic_ai import AIAgentService
from chat.factories import ChatConversationFactory, ChatProjectFactory
from chat.tests.utils import unwrap_instruction

pytestmark = pytest.mark.django_db


def _get_instruction_names(service):
    """Return the names of dynamic (callable) instructions registered on the conversation agent."""
    # pylint: disable=protected-access
    instructions = map(unwrap_instruction, service.conversation_agent._instructions)
    return [fn.__name__ for fn in instructions if callable(fn)]


def test_project_instructions_injected_when_present():
    """Test that project LLM instructions are injected as a dynamic agent instruction."""
    project = ChatProjectFactory(llm_instructions="Always answer in bullet points.")
    conversation = ChatConversationFactory(owner=project.owner, project=project)

    service = AIAgentService(conversation, user=conversation.owner)

    assert "project_instructions" in _get_instruction_names(service)
    # Verify the instruction returns the correct content
    instructions = service.conversation_agent._instructions  # pylint: disable=protected-access
    instruction_fn = next(
        fn
        for fn in map(unwrap_instruction, instructions)
        if callable(fn) and fn.__name__ == "project_instructions"
    )
    assert instruction_fn() == "Always answer in bullet points."


def test_project_instructions_not_injected_when_empty():
    """Test that empty project instructions are not injected."""
    project = ChatProjectFactory(llm_instructions="")
    conversation = ChatConversationFactory(owner=project.owner, project=project)

    service = AIAgentService(conversation, user=conversation.owner)

    assert "project_instructions" not in _get_instruction_names(service)


def test_project_instructions_not_injected_when_no_project():
    """Test that no project instruction is injected for standalone conversations."""
    conversation = ChatConversationFactory()

    service = AIAgentService(conversation, user=conversation.owner)

    assert "project_instructions" not in _get_instruction_names(service)
