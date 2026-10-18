from agents import function_tool
import crm


@function_tool
def update_contact(contact: str, email: str) -> str:
    """Update a CRM contact."""
    return crm.update_contact(contact, email=email)


@function_tool
def lookup_contact(contact: str) -> str:
    """Read only."""
    return crm.get_contact(contact)
