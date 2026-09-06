import time
from typing import TypedDict, Annotated
import operator

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from app.models.coder_model import coder_model as model, CODING_TOOLS
from app.config.settings import CODER_MAX_STEPS


class CoderState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    steps: int


system_prompt = f"""You are a specialist coding subagent, called in to help with a
task another AI decided needed real coding expertise. You have no internet access —
everything runs locally.

Your tools:
- python_runner: run Python code in an isolated sandbox (no network, resets each call).
  Save any output files to /sandbox/output/ and print the path afterward.
- read_text_file(path, start_line, end_line, location): read a range of lines from
  any plain-text file — source code, config, csv, markdown. location is 'input' for
  a user-uploaded file or 'output' for one previously saved.
- read_pdf(filename, start_page, end_page, location): read a page range from a
  text-based PDF.
- list_sandbox_files(location): list filenames in /sandbox/input or /sandbox/output.
  Use this before assuming a filename exists.

Hard rules:
1. Never claim code works without actually running it through python_runner first.
   Do not describe what code would output — only report what it actually printed.
2. If python_runner returns an error, read it, fix the code, and run it again.
   Do not give up after one failure.
3. You have at most {CODER_MAX_STEPS} tool-call steps. Work efficiently — don't
   re-read files you've already read, and don't run trivial variations of code
   that already worked.
4. Once you have a verified, working result, stop calling tools and give your
   final answer as plain text. Include the actual code you settled on and confirm
   it ran successfully.
5. You are not talking to the end user directly — your final answer will be read
   by another AI, so be precise and complete rather than conversational. State
   clearly what was verified and how.
"""

tool_node = ToolNode(list(CODING_TOOLS.values()))


def call_model(state: CoderState):
    start = time.perf_counter()
    response = model(state["messages"])
    elapsed = time.perf_counter() - start

    print(f"[coder] model responded in {elapsed:.2f}s (step {state['steps'] + 1}/{CODER_MAX_STEPS})")
    if response.tool_calls:
        for call in response.tool_calls:
            print(f"[coder]   -> Tool call: {call['name']}({call['args']})")

    return {
        "messages": [response],
        "steps": state["steps"] + 1,
    }


def tools_with_logging(state: CoderState):
    start = time.perf_counter()
    result = tool_node.invoke(state)
    elapsed = time.perf_counter() - start

    for msg in result["messages"]:
        tool_name = getattr(msg, "name", "unknown_tool")
        print(f"[coder]   <- Tool result: {tool_name} ran in {elapsed:.2f}s")

    return result


def tool_call_check(state: CoderState):
    if state["steps"] >= CODER_MAX_STEPS:
        return "end"
    if state["messages"][-1].tool_calls:
        return "tools"
    return "end"


class CoderAgent:
    def __init__(self):
        graph = StateGraph(CoderState)
        graph.add_node("model", call_model)
        graph.add_node("tools", tools_with_logging)

        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model",
            tool_call_check,
            {"tools": "tools", "end": END},
        )
        graph.add_edge("tools", "model")

        self.graph = graph.compile()

    def run(self, task: str) -> str:
        overall_start = time.perf_counter()

        initial_state = {
            "messages": [SystemMessage(content=system_prompt), HumanMessage(content=task)],
            "steps": 0,
        }
        result = self.graph.invoke(initial_state)

        total_elapsed = time.perf_counter() - overall_start
        print(f"[coder] subagent finished in {total_elapsed:.2f}s total, {result['steps']} step(s)")

        return result["messages"][-1].content