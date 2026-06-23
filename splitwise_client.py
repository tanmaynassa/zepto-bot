import requests
import os


class SplitwiseClient:
    """Simple Splitwise API client using API key auth."""

    BASE_URL = "https://secure.splitwise.com/api/v3.0"

    def __init__(self):
        self.api_key = os.environ["SPLITWISE_API_KEY"]
        self.my_user_id = None
        self.kalash_user_id = os.environ.get("SPLITWISE_KALASH_USER_ID")
        self.group_id = os.environ.get("SPLITWISE_GROUP_ID")  # optional
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def _get(self, endpoint: str, params: dict = None) -> dict:
        r = requests.get(f"{self.BASE_URL}/{endpoint}", headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, data: dict) -> dict:
        r = requests.post(f"{self.BASE_URL}/{endpoint}", headers=self.headers, data=data)
        r.raise_for_status()
        return r.json()

    def get_current_user(self) -> dict:
        """Get the authenticated user's info."""
        result = self._get("get_current_user")
        self.my_user_id = result["user"]["id"]
        return result["user"]

    def get_friends(self) -> list:
        """List all friends."""
        result = self._get("get_friends")
        return result["friends"]

    def find_kalash(self) -> dict | None:
        """Find Kalash in friends list if KALASH_USER_ID not set."""
        if self.kalash_user_id:
            return {"id": int(self.kalash_user_id)}

        friends = self.get_friends()
        for f in friends:
            name = f"{f.get('first_name', '')} {f.get('last_name', '')}".lower()
            if "kalash" in name:
                self.kalash_user_id = str(f["id"])
                return f

        return None

    def create_expense(
        self,
        description: str,
        total_cost: float,
        my_share: float,
        kalash_share: float,
        details: str = "",
    ) -> dict:
        """
        Create an expense where Tanmay paid the full amount
        and the split is as computed.
        """
        if not self.my_user_id:
            self.get_current_user()

        if not self.kalash_user_id:
            kalash = self.find_kalash()
            if not kalash:
                raise ValueError(
                    "Could not find Kalash in your Splitwise friends. "
                    "Set SPLITWISE_KALASH_USER_ID in your .env file."
                )

        cost_str = f"{total_cost:.2f}"
        my_paid = cost_str  # Tanmay paid everything
        my_owed = f"{my_share:.2f}"
        kalash_paid = "0.00"
        kalash_owed = f"{kalash_share:.2f}"

        data = {
            "cost": cost_str,
            "description": description,
            "details": details,
            "currency_code": "INR",
            "users__0__user_id": self.my_user_id,
            "users__0__paid_share": my_paid,
            "users__0__owed_share": my_owed,
            "users__1__user_id": int(self.kalash_user_id),
            "users__1__paid_share": kalash_paid,
            "users__1__owed_share": kalash_owed,
        }

        if self.group_id:
            data["group_id"] = int(self.group_id)

        result = self._post("create_expense", data)

        # Check for errors in response
        if "errors" in result and result["errors"]:
            raise ValueError(f"Splitwise error: {result['errors']}")

        return result


def build_expense_details(split: dict) -> str:
    """Build the notes/details string for the Splitwise expense.
    This is what Kalash sees when he opens the expense on Splitwise."""
    lines = ["— Order Breakdown —", ""]

    if split["my_items"]:
        lines.append("Tanmay's items:")
        for i in split["my_items"]:
            lines.append(f"  • {i['name']} — ₹{i['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['my_personal']:.2f}")
        lines.append("")

    if split["kalash_items"]:
        lines.append("Kalash's items:")
        for i in split["kalash_items"]:
            lines.append(f"  • {i['name']} — ₹{i['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['kalash_personal']:.2f}")
        lines.append("")

    if split["shared_items"]:
        lines.append("Shared items (50/50):")
        for i in split["shared_items"]:
            lines.append(f"  • {i['name']} — ₹{i['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['shared_total']:.2f} (₹{split['shared_each']:.2f} each)")
        lines.append("")

    lines.append("— Split —")
    lines.append(f"Tanmay paid: ₹{split['order_total']:.2f}")
    lines.append(f"Tanmay's share: ₹{split['my_share']:.2f}")
    lines.append(f"Kalash's share: ₹{split['kalash_share']:.2f}")

    return "\n".join(lines)
