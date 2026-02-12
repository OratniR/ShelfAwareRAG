import logging
from notion_client import Client, APIResponseError, APIErrorCode
from .config import settings
from .interfaces import ShoppingListClient

logger = logging.getLogger(__name__)


class NotionShoppingListClient(ShoppingListClient):
    """
    Concrete implementation of ShoppingListClient using Notion API.
    """

    def __init__(self):
        logger.info("Initializing NotionShoppingListClient...")
        self.notion: Client | None = None
        # Map configuration: data_source_id acts as the data_source_id
        self.data_source_id: str | None = settings.NOTION_DATASOURCE_ID
        self.item_prop: str = settings.NOTION_ITEM_PROPERTY_NAME
        self.checkbox_prop: str = settings.NOTION_CHECKBOX_PROPERTY_NAME

        self._initialize_client()

    def _initialize_client(self):
        """Initializes the Notion client using API Key and verifies datasource ID."""
        if settings.NOTION_API_KEY and self.data_source_id:
            try:
                self.notion = Client(
                    auth=settings.NOTION_API_KEY,
                    notion_version="2025-09-03",  
                )
                # Test connection by retrieving the datasource object
                # Replaced deprecated/incorrect data_sources.retrieve with datasources.retrieve
                self.notion.data_sources.retrieve(data_source_id=self.data_source_id)

                logger.info(
                    f"Successfully connected to Notion and verified datasource ID: {self.data_source_id}"
                )

            except APIResponseError as e:
                logger.error(
                    f"Failed to connect to Notion or retrieve datasource info: {e}",
                    exc_info=True,
                )
                self.notion = None  # Disable client on API error
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred during Notion initialization: {e}",
                    exc_info=True,
                )
                self.notion = None  # Disable on other errors
        else:
            logger.warning(
                f"Notion API Key or datasource ID not configured. Notion integration disabled.\n NOTION_API_KEY:{'***' if settings.NOTION_API_KEY else 'None'}\n self.data_source_id:{self.data_source_id}"
            )
            self.notion = None  # Ensure client is None if not configured

    def is_active(self) -> bool:
        """Checks if the Notion client is initialized and data_source_id is set."""
        return self.notion is not None and self.data_source_id is not None

    def _find_item_page(self, item_name: str) -> dict | None:
        """Finds a page in the datasource matching the item name."""
        if not self.is_active():
            logger.warning("Notion client inactive. Cannot query.")
            return None

        item_name_stripped = item_name.strip()
        logger.debug(
            f"Querying Notion for item: '{item_name_stripped}' using data_source_id: {self.data_source_id}"
        )

        try:
            response = self.notion.data_sources.query(
                data_source_id=self.data_source_id,
                filter={
                    "property": self.item_prop,
                    "title": {"equals": item_name_stripped},
                },
            )

            if response and response.get("results"):
                page = response["results"][0]
                logger.debug(
                    f"Found Notion page for '{item_name_stripped}': ID {page['id']}"
                )
                return page
            else:
                logger.debug(f"No Notion page found for '{item_name_stripped}'")
                return None
        except APIResponseError as e:
            if e.code == APIErrorCode.RateLimited:
                logger.warning(
                    "Notion API rate limit exceeded. Please wait before retrying."
                )
            elif e.code == APIErrorCode.ValidationError:
                logger.error(
                    f"Notion API Validation Error during query (check datasource schema/ID?): {e}"
                )
            else:
                logger.error(
                    f"API Error querying Notion datasource for '{item_name_stripped}': {e}",
                    exc_info=True,
                )
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error querying Notion datasource '{item_name_stripped}': {e}",
                exc_info=True,
            )
            return None

    def add_item(self, item_name: str) -> None:
        """Adds an item to the Notion shopping list or unchecks if it exists."""
        if not self.is_active():
            logger.warning("Notion client inactive. Cannot add item.")
            return

        item_name_stripped = item_name.strip()
        existing_page = self._find_item_page(item_name_stripped)

        try:
            if existing_page:
                logger.info(f"Calling pages.update for page_id: {existing_page['id']}")
                is_checked = existing_page["properties"][self.checkbox_prop]["checkbox"]
                if is_checked:
                    logger.info(
                        f"Item '{item_name_stripped}' exists but is checked. Unchecking in Notion."
                    )
                    self.notion.pages.update(
                        page_id=existing_page["id"],
                        properties={self.checkbox_prop: {"checkbox": False}},
                    )
                    logger.info("pages.update successful.")
                else:
                    logger.info(
                        f"Item '{item_name_stripped}' already exists and is unchecked in Notion."
                    )
            else:
                logger.info("Calling pages.create...")

                logger.info(f"Adding item '{item_name_stripped}' to Notion datasource.")
                self.notion.pages.create(
                    parent={
                        "data_source_id": self.data_source_id
                    },  # FIX: Changed data_source_id to data_source_id
                    properties={
                        self.item_prop: {
                            "title": [{"text": {"content": item_name_stripped}}]
                        },
                        self.checkbox_prop: {"checkbox": False},
                    },
                )
                logger.info("pages.create successful.")
        except APIResponseError as e:
            logger.error(
                f"API Error adding/updating item '{item_name_stripped}' in Notion: {e}",
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                f"Unexpected error adding/updating item '{item_name_stripped}' in Notion: {e}",
                exc_info=True,
            )

    def remove_item(self, item_name: str) -> None:
        """Marks an item as 'bought' (checks the checkbox) in the Notion shopping list."""
        if not self.is_active():
            logger.warning("Notion client inactive. Cannot remove item.")
            return

        item_name_stripped = item_name.strip()
        existing_page = self._find_item_page(item_name_stripped)

        if existing_page:
            is_checked = existing_page["properties"][self.checkbox_prop]["checkbox"]
            if not is_checked:
                logger.info(
                    f"Marking item '{item_name_stripped}' as checked in Notion."
                )
                try:
                    self.notion.pages.update(
                        page_id=existing_page["id"],
                        properties={self.checkbox_prop: {"checkbox": True}},
                    )
                except APIResponseError as e:
                    logger.error(
                        f"API Error checking item '{item_name_stripped}' in Notion: {e}",
                        exc_info=True,
                    )
                except Exception as e:
                    logger.error(
                        f"An unexpected error occurred checking item '{item_name_stripped}' in Notion: {e}",
                        exc_info=True,
                    )
            else:
                logger.info(
                    f"Item '{item_name_stripped}' is already checked in Notion."
                )
        else:
            logger.info(
                f"Item '{item_name_stripped}' not found in Notion. Cannot mark as checked."
            )
