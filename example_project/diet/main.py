
import akasha
import dotenv
from pathlib import Path

# Shared credentials live at example_project/.env.
dotenv.load_dotenv(Path(__file__).resolve().parent.parent / '.env')

KNOWLEDGE_FILE = Path(__file__).resolve().with_name("DIET-v1.md")
SYSTEM_PROMPT = """你是自然、直接、友善的飲食問答助手，請使用繁體中文回答。
如果參考文件能回答問題，請優先使用其中的內容；如果不能回答，簡單說明不知道，
不要自行補充沒有根據的資訊。直接回答使用者的問題，不要先說「根據參考資料」、
「根據文件」、「根據提供的內容」或交代你使用了哪些資料，除非使用者明確要求來源或依據。
不要重述問題，也不要加入與問題無關的免責說明。"""

def answer_question(question: str) -> str:
    """Answer one question using the diet knowledge base."""
    qa = akasha.ask(
        model="gemini:gemini-2.5-flash",
        system_prompt=SYSTEM_PROMPT,
        verbose=False,
        stream=False,
    )
    return qa(prompt=question, info=KNOWLEDGE_FILE)


def main() -> None:
    question = input("請輸入問題：")
    print(answer_question(question))


if __name__ == "__main__":
    main()
