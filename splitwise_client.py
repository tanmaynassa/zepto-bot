import requests
import os


class SplitwiseClient:
    """Simple Splitwise API client using API key auth."""

    BASE_URL = "https://secure.splitwise.com/api/v3.0"

    def __init__(self):
        self.api_key = os.environ["SPLITWISE_API_KEY"]
        self.my_user_id = None
        self.kalash_user_id = os.environ.get("SPLITWISE_KALASH_USER_ID")
        self.abhirag_user_id = os.environ.get("SPLITWISE_ABHIRAG_USER_ID")
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

    def _find_friend(self, search_name: str) -> dict | None:
        """Find a friend by name substring."""
        friends = self.get_friends()
        for f in friends:
            name = f"{f.get('first_name', '')} {f.get('last_name', '')}".lower()
            if search_name.lower() in name:
                return f
        return None

    def find_kalash(self) -> dict | None:
        """Find Kalash in friends list if KALASH_USER_ID not set."""
        if self.kalash_user_id:
            return {"id": int(self.kalash_user_id)}
        f = self._find_friend("kalash")
        if f:
            self.kalash_user_id = str(f["id"])
        return f

    def find_abhirag(self) -> dict | None:
        """Find Abhirag in friends list if ABHIRAG_USER_ID not set."""
        if self.abhirag_user_id:
            return {"id": int(self.abhirag_user_id)}
        # Splitwise name is "Abhi Rag Tata" — search for "abhi"
        f = self._find_friend("abhi")
        if f:
            self.abhirag_user_id = str(f["id"])
        return f

    def init_users(self):
        """Initialize all user IDs."""
        if not self.my_user_id:
            self.get_current_user()
        if not self.kalash_user_id:
            self.find_kalash()
        if not self.abhirag_user_id:
            self.find_abhirag()

    def create_expense(
        self,
        description: str,
        total_cost: float,
        my_share: float,
        kalash_share: float,
        abhirag_share: float = 0.0,
        details: str = "",
    ) -> dict:
        """
        Create an expense where Tanmay paid the full amount.
        Supports 2-person (Tanmay + Kalash) or 3-person (+ Abhirag) splits.
        """
        self.init_users()

        if not self.kalash_user_id:
            raise ValueError(
                "Could not find Kalash in your Splitwise friends. "
                "Set SPLITWISE_KALASH_USER_ID in your env vars."
            )

        cost_str = f"{total_cost:.2f}"

        data = {
            "cost": cost_str,
            "description": description,
            "details": details,
            "currency_code": "INR",
            # User 0: Tanmay (paid everything)
            "users__0__user_id": self.my_user_id,
            "users__0__paid_share": cost_str,
            "users__0__owed_share": f"{my_share:.2f}",
            # User 1: Kalash
            "users__1__user_id": int(self.kalash_user_id),
            "users__1__paid_share": "0.00",
            "users__1__owed_share": f"{kalash_share:.2f}",
        }

        # Only include Abhirag if he has items
        if abhirag_share > 0:
            if not self.abhirag_user_id:
                raise ValueError(
                    "Could not find Abhirag in your Splitwise friends. "
                    "Set SPLITWISE_ABHIRAG_USER_ID in your env vars."
                )
            data["users__2__user_id"] = int(self.abhirag_user_id)
            data["users__2__paid_share"] = "0.00"
            data["users__2__owed_share"] = f"{abhirag_share:.2f}"

        if self.group_id:
            data["group_id"] = int(self.group_id)

        result = self._post("create_expense", data)

        if "errors" in result and result["errors"]:
            raise ValueError(f"Splitwise error: {result['errors']}")

        return result


def build_expense_details(split: dict) -> str:
    """Build the notes/details string for the Splitwise expense."""
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

    if split.get("abhirag_items"):
        lines.append("Abhirag's items:")
        for i in split["abhirag_items"]:
            lines.append(f"  • {i['name']} — ₹{i['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['abhirag_personal']:.2f}")
        lines.append("")

    if split["shared_items"]:
        lines.append("Shared items (Tanmay & Kalash 50/50):")
        for i in split["shared_items"]:
            lines.append(f"  • {i['name']} — ₹{i['amount']:.2f}")
        lines.append(f"  Subtotal: ₹{split['shared_total']:.2f} (₹{split['shared_each']:.2f} each)")
        lines.append("")

    lines.append("— Split —")
    lines.append(f"Tanmay paid: ₹{split['order_total']:.2f}")
    lines.append(f"Tanmay's share: ₹{split['my_share']:.2f}")
    lines.append(f"Kalash's share: ₹{split['kalash_share']:.2f}")
    if split.get("abhirag_share", 0) > 0:
        lines.append(f"Abhirag's share: ₹{split['abhirag_share']:.2f}")

    return "\n".join(lines)
