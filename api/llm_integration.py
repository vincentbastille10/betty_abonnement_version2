import os
import requests
import yaml

TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")

def load_pack_prompt(pack_name):
    pack_path = f"data/packs/{pack_name}.yaml"
    with open(pack_path, "r") as f:
        return yaml.safe_load(f)["prompt"]

def query_llm(user_input, pack_name):
    prompt = load_pack_prompt(pack_name)
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "meta-llama/Meta-Llama-3-8B-Instruct",
        "max_tokens": 90,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input}
        ]
    }
    r = requests.post(TOGETHER_API_URL, headers=headers, json=data)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
