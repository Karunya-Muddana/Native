from app.runtime.runtime import AgentRuntime

def main():
    runtime = AgentRuntime()
    session_id = runtime.new_session()
    print("Hello! I am an agent designed to answer questions.")
    while True:
        text = input("\nEnter your question (or 'exit' to quit): ")
        if text.strip().lower() == "exit":
            break
        print(runtime.run(text, session_id)["answer"])

if __name__ == "__main__":
    main()