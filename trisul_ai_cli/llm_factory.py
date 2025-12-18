from dotenv import dotenv_values, set_key
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain_community.embeddings import VoyageEmbeddings




class LLMFactory:
    SUPPORTED_MODELS = {
        "gemini": {"llm": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"], 
                   "embedding": "models/gemini-embedding-001"
                },
        "openai": {
                    "llm": ["gpt-5.1", "gpt-5.1-mini", "gpt-5.1-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",  "gpt-3.5-turbo"], 
                    "embedding": "text-embedding-3-large"
                },
        "anthropic": {
                        "llm": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"]
                    },
        "voyageai": {
                        "embedding": "voyage-2"
                    }
    }

    def __init__(self, env_path = None, logging = None):
        self.env_path = env_path
        self.logging = logging
        self._load_config()

    def _load_config(self):
        self.config = dotenv_values(self.env_path)
        self.provider = self.config.get("TRISUL_AI_PROVIDER", "gemini")
        self.model_name = self.config.get("TRISUL_AI_MODEL")
        self.api_key = self.config.get(f"TRISUL_{self.provider.upper()}_API_KEY")

        # Embedding config
        self.embedding_model = self.config.get("TRISUL_EMBEDDING_MODEL")
        self.embedding_provider = self.config.get("TRISUL_EMBEDDING_PROVIDER")
        
        # Fallback: Infer embedding provider if not set but model is set
        if self.embedding_model and not self.embedding_provider:
            for prov, data in self.SUPPORTED_MODELS.items():
                if data.get("embedding") == self.embedding_model:
                    self.embedding_provider = prov
                    break
        
        self.embedding_api_key = self.config.get(f"TRISUL_{str(self.embedding_provider).upper()}_API_KEY") if self.embedding_provider else None

        self.logging.info(f"[LLMFactory] Loaded config: provider={self.provider}, model={self.model_name}, embedding_model={self.embedding_model}, embedding_provider={self.embedding_provider}")




    def get_llm(self):
        self._load_config() # Reload in case it changed
        self.logging.info(f"[LLMFactory] Getting LLM for provider {self.provider} with model {self.model_name}")
        
        if not self.api_key:
            self.logging.warning("[LLMFactory] API key not found. Please set it using the 'set_api_key' method.")
            return None

        if self.provider == "gemini":
            return ChatGoogleGenerativeAI(model=self.model_name, google_api_key=self.api_key)
        elif self.provider == "openai":
            return ChatOpenAI(model=self.model_name, api_key=self.api_key)
        elif self.provider == "anthropic":
            return ChatAnthropic(model=self.model_name, api_key=self.api_key)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def get_embedding_llm(self):
        self._load_config()
        if not self.embedding_model or not self.embedding_provider:
            return None
            
        if not self.embedding_api_key:
             self.logging.warning(f"[LLMFactory] Embedding API key for {self.embedding_provider} not found.")
             return None

        if self.embedding_provider == "gemini":
            return GoogleGenerativeAIEmbeddings(model=self.embedding_model, google_api_key=self.embedding_api_key)
        elif self.embedding_provider == "openai":
            return OpenAIEmbeddings(model=self.embedding_model, api_key=self.embedding_api_key)
        elif self.embedding_provider == "voyageai":
            return VoyageEmbeddings(model=self.embedding_model, voyage_api_key=self.embedding_api_key)
        else:
            return None

    def set_provider(self, provider: str):
        if provider not in self.SUPPORTED_MODELS:
            raise ValueError(f"Unsupported provider: {provider}")
        
        self.provider = provider
        set_key(self.env_path, "TRISUL_AI_PROVIDER", provider)
        self.logging.info(f"[LLMFactory] Provider set to {provider}")
        self._load_config()

    def set_model(self, model_name: str):
        self.model_name = model_name
        set_key(self.env_path, "TRISUL_AI_MODEL", model_name)
        self.logging.info(f"[LLMFactory] Model set to {model_name}")
        self._load_config()

    def set_embedding_model(self, model_name: str):
        # Infer provider
        provider = None
        for prov, data in self.SUPPORTED_MODELS.items():
            if data.get("embedding") == model_name:
                provider = prov
                break
        
        set_key(self.env_path, "TRISUL_EMBEDDING_MODEL", model_name)
        if provider:
            set_key(self.env_path, "TRISUL_EMBEDDING_PROVIDER", provider)
            
        self.logging.info(f"[LLMFactory] Embedding model set to {model_name} (provider: {provider})")
        self._load_config()

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        set_key(self.env_path, f"TRISUL_{self.provider.upper()}_API_KEY", api_key)
        self.logging.info(f"[LLMFactory] API key updated for provider {self.provider}")
        self._load_config()
        


    def get_all_models(self):
        """Return a dictionary mapping each provider to its list of supported models.

        This allows callers to present a unified view of all available models across
        providers without needing to know the current provider.
        """
        self.logging.info("[LLMFactory] Retrieving all supported models across providers")
        return self.SUPPORTED_MODELS

    def get_all_embedding_models(self):
        models = []
        for prov, data in self.SUPPORTED_MODELS.items():
            if "embedding" in data:
                models.append((prov, data["embedding"]))
        return models

    def set_model_by_name(self, model_name: str):
        """Set both the provider and model based on a model name.

        The method searches through ``SUPPORTED_MODELS`` to find which provider
        supports the supplied ``model_name``. If found, it updates the provider
        (persisting to ``.env``) and the model accordingly. If the model is not
        found, a ``ValueError`` is raised.
        """
        # Find the provider that contains the model
        provider_found = None
        for prov, models_dict in self.SUPPORTED_MODELS.items():
            if model_name in models_dict.get("llm", []):
                provider_found = prov
                break
        if provider_found is None:
            raise ValueError(f"Model '{model_name}' is not supported by any provider")
        # Update provider and model using existing helpers to ensure env persistence
        if provider_found != self.provider or "TRISUL_AI_PROVIDER" not in self.config:
            self.set_provider(provider_found)
        # set_model will also persist the model name
        self.set_model(model_name)
        self.logging.info(f"[LLMFactory] Model and provider set to {model_name} / {provider_found}")

        # Handle embedding model logic
        provider_embedding = self.SUPPORTED_MODELS[provider_found].get("embedding")
        
        should_update_embedding = False
        
        if not self.embedding_model:
            # No embedding model set yet.
            if provider_embedding:
                should_update_embedding = True
        
        if should_update_embedding:
            self.set_embedding_model(provider_embedding)
            
        return should_update_embedding
        
    def get_current_provider(self):
        self.logging.info(f"[LLMFactory] Current provider: {self.provider}")
        return self.provider
        
    def get_current_model(self):
        self.logging.info(f"[LLMFactory] Current model: {self.model_name}")
        return self.model_name
        
    def get_current_api_key(self):
        self.logging.info(f"[LLMFactory] API key retrieved: {'set' if self.api_key else 'none'}")
        return self.api_key

    def get_current_embedding_provider(self):
        return self.embedding_provider

    def get_current_embedding_api_key(self):
        return self.embedding_api_key

    def set_api_key_for_provider(self, provider: str, api_key: str):
        set_key(self.env_path, f"TRISUL_{provider.upper()}_API_KEY", api_key)
        self.logging.info(f"[LLMFactory] API key updated for provider {provider}")
        self._load_config()
