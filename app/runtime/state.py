from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    # How many times this turn the model has described an action instead of
    # taking it. Bounded, and reset whenever a tool actually runs, so a stalling
    # model is pushed back into the loop a few times but can never spin there.
    stalls: int