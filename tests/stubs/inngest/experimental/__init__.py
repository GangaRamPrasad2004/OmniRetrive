class OpenAIAdapter:
    def __init__(self, **kwargs):
        self.options = kwargs


class OpenAI:
    Adapter = OpenAIAdapter


class AI:
    openai = OpenAI()


ai = AI()
