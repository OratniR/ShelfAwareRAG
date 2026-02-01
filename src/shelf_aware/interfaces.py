from abc import ABC, abstractmethod


class ShoppingListClient(ABC):
    """
    Abstract interface for an external shopping list client.
    Follows Dependency Inversion Principle.
    """

    @abstractmethod
    def is_active(self) -> bool:
        """Checks if the client is configured and active."""
        pass

    @abstractmethod
    def add_item(self, item_name: str) -> None:
        """Adds an item to the shopping list (e.g., when inventory runs out)."""
        pass

    @abstractmethod
    def remove_item(self, item_name: str) -> None:
        """Removes an item from the shopping list (e.g., when item is added to inventory)."""
        pass
