# ShelfAwareRAG: A Private, Voice-Controlled Inventory System

## 1\. Objective

ShelfAwareRAG is a personal inventory management system designed to answer one simple question: "Where did I put that?" It allows a user to record the location of items using natural language voice commands via Siri and query the system to find them later. The entire system runs locally on a Raspberry Pi 5, ensuring 100% data privacy.

## 2\. Uniqueness of This System

This project stands out due to its unique combination of local-first AI, intuitive interface, and advanced backend logic on low-cost hardware.

  * **100% Private & Local-First:** All data, models, and processing reside on a personal Raspberry Pi. No data ever leaves the local network, guaranteeing absolute privacy.
  * **Hands-Free Voice Interface:** Uses Apple's Siri Shortcuts as a seamless frontend, allowing for natural, conversational commands like "I put the soy sauce in the fridge" or "Where are my keys?"
  * **LLM-Powered Intent Classification:** Goes beyond a simple RAG (Retrieval-Augmented Generation) system. It uses a local Japanese LLM to perform intent classification, distinguishing between requests to **add**, **query**, or **delete** items from the inventory based on unstructured sentences.
  * **Efficient on Low-Cost Hardware:** The entire stack is optimized to run efficiently on an affordable Raspberry Pi 5 with 8GB of RAM, making it an accessible project.
  * **Maintainable & Transferable Architecture:** The project is containerized using Docker and Docker Compose, ensuring the environment is reproducible and easily transferable to new hardware with a single command.

## 3\. System Architecture

The system is architected as a client-server model running on a local network.

  * **Frontend (Client):** An iPhone running a custom Siri Shortcut. The shortcut captures the user's voice command, converts it to text, and sends it as a POST request to the backend.
  * **Backend (Server):** A Raspberry Pi 5 with a static IP address on the local network. It runs two main services managed by Docker Compose:
    1.  **`rag-api` Container:** A FastAPI application that serves as the main endpoint. It receives requests, orchestrates the RAG pipeline, and handles all business logic.
    2.  **`llm-server` Container:** A `llama-cpp-python` server dedicated to running the local LLM, providing an OpenAI-compatible API for inference.

The internal workflow is as follows:

1.  Siri Shortcut sends a user's utterance (e.g., "醤油はどこ？") to the `rag-api`.
2.  The `rag-api` sends this text to the `llm-server` to classify the intent (`query`, `add`, or `delete`) and extract relevant entities (`item_name`, `location`).
3.  Based on the intent, the `rag-api` performs the required action (e.g., searches the vector database for a `query` or inserts data for an `add`).
4.  For queries, relevant context from the database is combined with the original question and sent back to the `llm-server` to generate a natural language answer.
5.  The final answer is sent back to the Siri Shortcut, which speaks it to the user.

<!-- end list -->


## 4\. Technology Stacks

  * **Hardware:** Raspberry Pi 5 (8GB RAM)
  * **OS & Deployment:** Raspberry Pi OS (64-bit), Docker, Docker Compose
  * **Backend Framework:** Python 3.13, FastAPI, Uvicorn
  * **Package Management:** `uv`
  * **LLM:** Google Gemma 2 (2B Japanese Instruct GGUF, `gemma-2-2b-jpn-it-q4km.gguf`)
  * **Inference Server:** `llama-cpp-python` (with OpenBLAS for CPU acceleration)
  * **Embedding Model:** `intfloat/multilingual-e5-small`
  * **Vector Database:** ChromaDB
  * **Frontend Interface:** Apple Siri Shortcuts

## 5\. Challenges & Solutions

This project involved significant debugging and iterative refinement, which is a key part of the development story.

1.  **Dependency Issues:** The initial choice, `sqlite-vss`, lacked support for the Pi's ARM64 architecture.
      * **Solution:** Pivoted to `ChromaDB`, a pure-Python vector database that is hardware-agnostic and supports CRUD operations.
2.  **Docker Build Failures:** The minimal `python:slim` Docker image was missing necessary build tools.
      * **Solution:** Iteratively added `curl`, `build-essential`, and `cmake` to the `Dockerfile` and correctly configured `CMAKE_ARGS` to compile `llama-cpp-python` with OpenBLAS support.
3.  **Model Incompatibility:** A pre-built `llama-cpp-python` Docker image was incompatible with the Pi's architecture.
      * **Solution:** Modified the `docker-compose.yml` to build *both* services from a single, unified `Dockerfile`, ensuring a consistent and compatible environment.
4.  **LLM Reliability (The Core Challenge):** The most significant challenge was getting a small local LLM to reliably classify intent and produce perfectly formatted JSON.
      * **Problem:** Initial models like `OpenCALM-3B` produced garbage output. `Qwen1.5` misclassified intent and generated malformed JSON (e.g., extra spaces in keys like `'item_ name'`).
      * **Solution:** This was solved through a multi-step process:
        1.  **Model Experimentation:** Systematically tested multiple models (OpenCALM -\> Qwen1.5 -\> Qwen2 -\> **Gemma 2**), finally landing on a model capable of the task.
        2.  **Iterative Prompt Engineering:** Refined the system prompt multiple times, adding clear instructions, few-shot examples, and structural tags (`<json_schema>`) to guide the model.
        3.  **Code-Side Robustness:** Implemented a key sanitization function (`sanitize_dict_keys`) to programmatically fix minor LLM typos and used `.get()` for safe dictionary access to prevent crashes.
        4.  **Model-Specific Fixes:** Discovered the final Gemma 2 model did not support the "system" role and adapted the code to send all instructions within a single "user" message.
5.  **Performance:** Initial concerns about speed were addressed by monitoring system resources.
      * **Solution:** Used `htop` to confirm the process was CPU-bound, not RAM-bound (no swapping). Optimized `llama-cpp-python` with thread counts and confirmed BLAS was active, achieving the best possible performance on the hardware.

## 6\. Future Prospects
  * **More Efficient Models:** As new, even more efficient small language models are released, experiment with them to potentially improve speed and accuracy further.
  * **Implement "Modify" Intent:** Add functionality to change an item's location (e.g., "I moved the keys to the red box").
  * **Add Timestamps:** Automatically log when an item's location is added or updated.
  * **Web UI:** Create a simple web interface (e.g., using FastAPI's static file serving) to view the entire inventory.


## 7\. ShelfAwareRAG Setup Guide
This guide explains how to set up and run the ShelfAwareRAG application using Docker on a Raspberry Pi 5 (or a similar Linux system).

Prerequisites
Hardware: Raspberry Pi 5 (8GB RAM recommended) or another Linux machine.

OS: Raspberry Pi OS (64-bit) or a similar Debian-based Linux distribution.

Software:

Git: To clone the repository. (sudo apt update && sudo apt install git -y)

Docker & Docker Compose: Install using the official script:

Bash

curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ${USER}
# Log out and log back in after this step!
Setup Steps
Clone the Repository: Open a terminal and clone your project from GitHub. Replace <your-repo-url> with the actual URL.

Bash

git clone <your-repo-url>
cd ShelfAwareRAG # Or your repository's name
Download the LLM Model: Download the required GGUF model file into the data directory. (Make sure the docker-compose.yml points to the correct filename).

Bash

# Example using Gemma 2 2B Instruct Q4_K_M
wget -O data/gemma-2-2b-jpn-it-q4km.gguf \
https://huggingface.co/grapevine-AI/gemma-2-2b-jpn-it-gguf/resolve/main/gemma-2-2B-jpn-it-Q4_K_M.gguf
(Optional) Create .env File: If you add any API keys or configurable settings later, create a .env file in the project root and add them there (e.g., MY_API_KEY=...). Currently, this project doesn't strictly require a .env file.

Build the Docker Image: This command builds the container image defined in the Dockerfile. It compiles dependencies and copies your code. This might take several minutes the first time.

Bash

docker compose build
Run the Application: This command starts both the rag-api and llm-server containers in the background.

Bash

docker compose up -d
The application will now be running and accessible on port 8000 of your Raspberry Pi's IP address or hostname (e.g., http://raspberrypi.local:8000).

Siri Shortcut Configuration
Find Pi's Hostname/IP: Use ip addr show or try raspberrypi.local. Assign a static IP via your router if possible.

Create Shortcut: On your iPhone, use the Shortcuts app to create a new shortcut that:

Asks for text input (your query/statement).

Sends a POST request to http://<your-pi-address>:8000/dispatch with a JSON body like {"text": "your input"}.

Parses the JSON response (e.g., {"answer": "..."}) and speaks the "answer" value.

Important: Increase the timeout for the Get Contents of URL action to 60-90 seconds.

Managing the Application
View Logs: docker compose logs -f rag-api or docker compose logs -f llm-server

Stop Application: docker compose down

Restart Application: docker compose up -d

Update Code:

git pull

docker compose build (Only if code or dependencies changed)

docker compose up -d --force-recreate
