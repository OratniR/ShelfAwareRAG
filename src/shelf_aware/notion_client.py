# src/shelf_aware/notion_client.py
import logging
from notion_client import Client, APIResponseError, APIErrorCode
from .config import settings

logger = logging.getLogger(__name__)

class NotionClient:
    def __init__(self):
        logger.info("Initializing NotionClient...")
        self.notion: Client | None = None
        # Directly store data_source_id from settings
        self.data_source_id: str | None = settings.NOTION_DATASOURCE_ID
        self.item_prop: str = settings.NOTION_ITEM_PROPERTY_NAME
        self.checkbox_prop: str = settings.NOTION_CHECKBOX_PROPERTY_NAME

        self._initialize_client()

    def _initialize_client(self):
        """Initializes the Notion client using API Key and verifies Data Source ID."""
        if settings.NOTION_API_KEY and self.data_source_id:
            try:
                self.notion = Client(
                    auth=settings.NOTION_API_KEY,
                    notion_version="2025-09-03" # Specify API version
                )
                # Test connection by retrieving the Data Source object itself
                # This ensures the API key and Data Source ID are valid
                ds_info = self.notion.data_sources.retrieve(data_source_id=self.data_source_id)
                ds_title = "N/A"
                # Extract title safely if available (Note: retrieve data source might not return title directly, might need parent DB title if needed)
                # For now, just confirming the retrieve works is enough
                logger.info(f"Successfully connected to Notion and verified Data Source ID: {self.data_source_id}")

            except APIResponseError as e:
                logger.error(f"Failed to connect to Notion or retrieve data source info: {e}", exc_info=True)
                self.notion = None # Disable client on API error
            except Exception as e:
                logger.error(f"An unexpected error occurred during Notion initialization: {e}", exc_info=True)
                self.notion = None # Disable on other errors
        else:
            logger.warning(f"Notion API Key or Data Source ID not configured. Notion integration disabled.\n NOTION_API_KEY:{settings.NOTION_API_KEY}\n self.data_source_id:{self.data_source_id}")
            self.notion = None # Ensure client is None if not configured

    def is_active(self) -> bool:
        """Checks if the Notion client is initialized and data_source_id is set."""
        # data_source_id is checked implicitly by _initialize_client success
        return self.notion is not None

    def _find_item_page(self, item_name: str) -> dict | None:
        """Finds a page in the data source matching the item name."""
        if not self.is_active():
            logger.warning("Notion client inactive. Cannot query.")
            return None

        item_name_stripped = item_name.strip()
        logger.debug(f"Querying Notion for item: '{item_name_stripped}' using data_source_id: {self.data_source_id}")

        try:
            # Use data_sources.query with the configured data_source_id
            response = self.notion.data_sources.query(
                data_source_id=self.data_source_id, # Use the directly configured ID
                filter={
                    "property": self.item_prop,
                    "title": {
                        "equals": item_name_stripped
                    }
                }
            )
            # ... (rest of the find logic remains the same) ...
            if response and response.get("results"):
                page = response["results"][0]
                logger.debug(f"Found Notion page for '{item_name_stripped}': ID {page['id']}")
                return page
            else:
                logger.debug(f"No Notion page found for '{item_name_stripped}'")
                return None
        except APIResponseError as e:
            # ... (error handling remains the same) ...
            if e.code == APIErrorCode.RateLimited:
                logger.warning("Notion API rate limit exceeded. Please wait before retrying.")
            elif e.code == APIErrorCode.ValidationError:
                 logger.error(f"Notion API Validation Error during query (check data source schema/ID?): {e}")
            else:
                logger.error(f"API Error querying Notion data source for '{item_name_stripped}': {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Unexpected error querying Notion data source '{item_name_stripped}': {e}", exc_info=True)
            return None


    def add_item(self, item_name: str):
        """Adds an item to the Notion shopping list or unchecks if it exists."""
        if not self.is_active():
            logger.warning("Notion client inactive. Cannot add item.")
            return

        item_name_stripped = item_name.strip()
        existing_page = self._find_item_page(item_name_stripped)

        try:
            if existing_page:
                logger.info(f"Calling pages.update for page_id: {existing_page['id']}") # API呼び出し前ログ
                # ... (logic for unchecking existing item remains the same) ...
                is_checked = existing_page["properties"][self.checkbox_prop]["checkbox"]
                if is_checked:
                    logger.info(f"Item '{item_name_stripped}' exists but is checked. Unchecking in Notion.")
                    self.notion.pages.update(
                        page_id=existing_page["id"],
                        properties={
                            self.checkbox_prop: {"checkbox": False}
                        }
                    )
                    logger.info("pages.update successful.") # API呼び出し成功ログ
                else:
                    logger.info(f"Item '{item_name_stripped}' already exists and is unchecked in Notion.")
            else:
                logger.info("Calling pages.create...") # API呼び出し前ログ
                
                # ... (logic for creating new item remains the same, using self.data_source_id) ...
                logger.info(f"Adding item '{item_name_stripped}' to Notion database.")
                self.notion.pages.create(
                    parent={"data_source_id": self.data_source_id},
                    # propertiesはキーワード引数として渡す
                    properties={
                        self.item_prop: {
                            "title": [{"text": {"content": item_name_stripped}}]
                        },
                        self.checkbox_prop: {"checkbox": False}
                    }
                )
                logger.info("pages.create successful.") # API呼び出し成功ログ
        except APIResponseError as e:
            logger.error(f"API Error adding/updating item '{item_name_stripped}' in Notion: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error adding/updating item '{item_name_stripped}' in Notion: {e}", exc_info=True)


    def remove_item(self, item_name: str):
        """Marks an item as 'bought' (checks the checkbox) in the Notion shopping list."""
        # ... (this method remains the same, relies on _find_item_page) ...
        if not self.is_active():
            logger.warning("Notion client inactive. Cannot remove item.")
            return

        item_name_stripped = item_name.strip()
        existing_page = self._find_item_page(item_name_stripped)

        if existing_page:
            is_checked = existing_page["properties"][self.checkbox_prop]["checkbox"]
            if not is_checked:
                logger.info(f"Marking item '{item_name_stripped}' as checked in Notion.")
                try:
                    self.notion.pages.update(
                        page_id=existing_page["id"],
                        properties={
                            self.checkbox_prop: {"checkbox": True}
                        }
                    )
                except APIResponseError as e:
                    logger.error(f"API Error checking item '{item_name_stripped}' in Notion: {e}", exc_info=True)
                except Exception as e:
                     logger.error(f"An unexpected error occurred checking item '{item_name_stripped}' in Notion: {e}", exc_info=True)
            else:
                logger.info(f"Item '{item_name_stripped}' is already checked in Notion.")
        else:
            logger.info(f"Item '{item_name_stripped}' not found in Notion. Cannot mark as checked.")