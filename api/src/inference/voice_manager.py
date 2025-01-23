"""Voice pack management and caching."""

import os
from typing import Dict, List, Optional

import torch
from loguru import logger

from ..core import paths
from ..core.config import settings
from ..structures.model_schemas import VoiceConfig


class VoiceManager:
    """Manages voice loading and operations."""

    def __init__(self, config: Optional[VoiceConfig] = None):
        """Initialize voice manager.
        
        Args:
            config: Optional voice configuration
        """
        self._config = config or VoiceConfig()
        self._voice_cache: Dict[str, torch.Tensor] = {}

    def get_voice_path(self, voice_name: str) -> Optional[str]:
        """Get path to voice file.
        
        Args:
            voice_name: Name of voice
            
        Returns:
            Path to voice file if exists, None otherwise
        """
        api_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        voice_path = os.path.join(api_dir, settings.voices_dir, f"{voice_name}.pt")
        return voice_path if os.path.exists(voice_path) else None

    async def load_voice(self, voice_name: str, device: str = "cpu") -> torch.Tensor:
        """Load voice tensor.
        
        Args:
            voice_name: Name of voice to load
            device: Device to load voice on
            
        Returns:
            Voice tensor
            
        Raises:
            RuntimeError: If voice loading fails
        """
        voice_path = self.get_voice_path(voice_name)
        if not voice_path:
            raise RuntimeError(f"Voice not found: {voice_name}")

        # Check cache first
        cache_key = f"{voice_path}_{device}"
        if self._config.use_cache and cache_key in self._voice_cache:
            return self._voice_cache[cache_key]

        # Load voice tensor
        try:
            voice = await paths.load_voice_tensor(voice_path, device=device)
        except Exception as e:
            raise RuntimeError(f"Failed to load voice {voice_name}: {e}")

        # Cache if enabled
        if self._config.use_cache:
            self._manage_cache()
            self._voice_cache[cache_key] = voice
            logger.debug(f"Cached voice: {voice_name} on {device}")

        return voice

    def _manage_cache(self) -> None:
        """Manage voice cache size using simple LRU."""
        if len(self._voice_cache) >= self._config.cache_size:
            # Remove least recently used voice
            oldest = next(iter(self._voice_cache))
            del self._voice_cache[oldest]
            torch.cuda.empty_cache()  # Clean up GPU memory if needed
            logger.debug(f"Removed LRU voice from cache: {oldest}")

    async def combine_voices(self, voices: List[str], device: str = "cpu") -> str:
        """Combine multiple voices into a new voice.
        
        Args:
            voices: List of voice names to combine
            device: Device to load voices on
            
        Returns:
            Name of combined voice
            
        Raises:
            ValueError: If fewer than 2 voices provided
            RuntimeError: If voice combination fails
        """
        if len(voices) < 2:
            raise ValueError("At least 2 voices are required for combination")

        # Load voices
        voice_tensors: List[torch.Tensor] = []
        for voice in voices:
            try:
                voice_tensor = await self.load_voice(voice, device)
                voice_tensors.append(voice_tensor)
            except Exception as e:
                raise RuntimeError(f"Failed to load voice {voice}: {e}")

        try:
            # Combine voices
            combined_name = "_".join(voices)
            combined_tensor = torch.mean(torch.stack(voice_tensors), dim=0)
            
            # Get api directory path
            api_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            voices_dir = os.path.join(api_dir, settings.voices_dir)
            os.makedirs(voices_dir, exist_ok=True)
            
            # Only save to disk if local voice saving is allowed
            if settings.allow_local_voice_saving:
                combined_path = os.path.join(voices_dir, f"{combined_name}.pt")
                try:
                    torch.save(combined_tensor, combined_path)
                    # Cache the new combined voice with disk path
                    self._voice_cache[f"{combined_path}_{device}"] = combined_tensor
                except Exception as e:
                    raise RuntimeError(f"Failed to save combined voice: {e}")
            else:
                # Just cache the combined voice in memory without saving to disk
                self._voice_cache[f"{combined_name}_{device}"] = combined_tensor

            return combined_name

        except Exception as e:
            raise RuntimeError(f"Failed to combine voices: {e}")

    async def list_voices(self) -> List[str]:
        """List available voices.
        
        Returns:
            List of voice names, including both disk-saved and in-memory combined voices
        """
        voices = set()  # Use set to avoid duplicates
        try:
            # Get voices from disk
            api_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            voices_dir = os.path.join(api_dir, settings.voices_dir)
            os.makedirs(voices_dir, exist_ok=True)
            
            for entry in os.listdir(voices_dir):
                if entry.endswith(".pt"):
                    voices.add(entry[:-3])
            
            # Add in-memory combined voices from cache
            for cache_key in self._voice_cache:
                # Extract voice name from cache key (format: "name_device" or "path_device")
                voice_name = cache_key.split("_")[0]
                if "/" in voice_name:  # It's a path
                    voice_name = os.path.basename(voice_name)[:-3]  # Remove .pt extension
                voices.add(voice_name)
                
        except Exception as e:
            logger.error(f"Error listing voices: {e}")
        return sorted(list(voices))

    def validate_voice(self, voice_path: str) -> bool:
        """Validate voice file.
        
        Args:
            voice_path: Path to voice file
            
        Returns:
            True if valid, False otherwise
        """
        try:
            if not os.path.exists(voice_path):
                return False
            voice = torch.load(voice_path, map_location="cpu")
            return isinstance(voice, torch.Tensor)
        except Exception:
            return False

    @property
    def cache_info(self) -> Dict[str, int]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache info
        """
        return {
            'size': len(self._voice_cache),
            'max_size': self._config.cache_size
        }


# Global singleton instance
_manager_instance = None


async def get_manager(config: Optional[VoiceConfig] = None) -> VoiceManager:
    """Get global voice manager instance.
    
    Args:
        config: Optional voice configuration
        
    Returns:
        VoiceManager instance
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = VoiceManager(config)
    return _manager_instance