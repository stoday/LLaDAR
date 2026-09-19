import os

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from workflow import record


def answer(question: str, knowledge: str) -> str:
    """Internal model boundary. Never use this as the public test entrypoint."""
    @tool
    def shop_policy(topic: str) -> str:
        """Read the configured shop's refund and shipping policies."""
        record("tool_called", topic)
        return knowledge

    model = ChatGoogleGenerativeAI(model=os.getenv("EXAMPLE_MODEL", "gemini-3-flash-preview"),
                                   api_key=os.environ["GEMINI_API_KEY"])
    agent = create_agent(model=model, tools=[shop_policy],
                         system_prompt="你是商店客服，回答前必須使用 shop_policy 查詢。依政策用繁體中文簡短回答；資料不足時請說明。")
    response = agent.invoke({"messages": [{"role": "user", "content": question}]})
    content = response["messages"][-1].content
    text = content if isinstance(content, str) else "".join(
        block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text")
    record("model_answered")
    return text
