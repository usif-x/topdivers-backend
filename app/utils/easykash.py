# app/utils/easykash_utils.py

import hashlib  # <-- NEW IMPORT
import hmac  # <-- NEW IMPORT
import json
import random

import requests

from app.core.config import settings

private_key = settings.EASYKASH_PRIVATE_KEY
secret_key = settings.EASYKASH_SECRET_KEY


class EasyKash:
    def __init__(self, private_key, secret_key):
        self.private_key = private_key
        self.secret_key = secret_key

    @staticmethod
    def generate_customer_reference(user_id: int) -> str:
        random_digits1 = str(random.randint(10000, 99999))
        random_digits2 = str(random.randint(10000, 99999))
        # Making it more robust and unique
        return f"{random_digits1}{user_id}{random_digits2}"

    def create_payment(self, payment_data, user_id: int):
        url = "https://back.easykash.net/api/directpayv1/pay"
        headers = {
            "Content-Type": "application/json",
            "authorization": self.private_key,
        }

        customer_ref = self.generate_customer_reference(user_id)

        data = {
            "amount": payment_data["amount"],
            "currency": payment_data["currency"],
            "paymentOptions": [4, 5, 6, 31, 35],
            "cashExpiry": payment_data.get("cash_expiry", 3),  # Added a default value
            "name": payment_data["buyer_name"],
            "email": payment_data["buyer_email"],
            "mobile": payment_data["buyer_phone"],
            "redirectUrl": "https://topdivers.online/pay",
            "customerReference": customer_ref,
        }

        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            response_json = response.json()
            data_to_return = {
                "success": True,
                # Return the customerReference we generated
                "customer_reference": customer_ref,
                "pay_url": response_json.get("redirectUrl"),
                # Return EasyKash's own reference if they provide one on creation
                "easykash_reference": response_json.get("easykashReference"),
            }
            return data_to_return
        else:
            return {
                "success": False,
                "error": f"Failed to create payment. Status code: {response.status_code}",
                "details": response.text,
            }

    def inquire_payment(self, customer_reference: str):
        url = "https://back.easykash.net/api/cash-api/inquire"
        headers = {
            "Content-Type": "application/json",
            "authorization": self.private_key,
        }
        # The inquire endpoint likely expects JSON
        data = {"customerReference": customer_reference}
        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            return response.json()
        else:
            return {
                "error": f"Failed to inquire payment. Status code: {response.status_code}",
                "details": response.text,
            }

    def verify_callback(self, payload: dict, secret_key: str) -> bool:
        # Extract data from the payload
        product_code = payload.get("ProductCode")
        amount = payload.get("Amount")
        product_type = payload.get("ProductType")
        payment_method = payload.get("PaymentMethod")
        status = payload.get("status")
        easykash_ref = payload.get("easykashRef")
        customer_reference = payload.get("customerReference")
        signature_hash = payload.get("signatureHash")

        # Prepare data for verification (concatenated string)
        data_to_secure = [
            str(product_code),
            str(amount),
            str(product_type),
            str(payment_method),
            str(status),
            str(easykash_ref),
            str(customer_reference),
        ]
        data_str = "".join(data_to_secure)

        # Generate HMAC SHA-512 hash
        calculated_signature = hmac.new(
            secret_key.encode("utf-8"), data_str.encode("utf-8"), hashlib.sha512
        ).hexdigest()

        print("Concatenated data =", data_str)
        print("Calculated signature =", calculated_signature)
        print("Received signature   =", signature_hash)

        return calculated_signature == signature_hash

    # --- 3. Example Verification Function ---


# Create a single instance to be used throughout the app
easykash_client = EasyKash(private_key=private_key, secret_key=secret_key)
