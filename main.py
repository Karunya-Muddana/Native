from app.runtime.runtime import AgentRuntime


runtime = AgentRuntime()

result = runtime.run("""The T-18 inspection report and its measurement workbook are in the sandbox.
Review them against our vessel inspection standard, determine whether this
finding requires escalation, and if so tell me the category, the required
action and interval, and exactly who has to approve and countersign it.""")

print(result["answer"])