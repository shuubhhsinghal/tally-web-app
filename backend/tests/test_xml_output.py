import asyncio
from backend.routers.purchase_item import post_purchase_item, PurchaseItemPostRequest, PurchaseItemRow

async def main():
    payload = PurchaseItemPostRequest(
        supplier="Aditya Agency",
        invoice_number="GST/005154",
        tally_date="25/07/2026",
        cost_center="Mahagun",
        cgst=0.0,
        sgst=0.0,
        igst=0.0,
        rounding_off=0.0,
        items=[
            PurchaseItemRow(
                name="DM PASANTA PENNE BOGO WW (500G*20)RS",
                qty=6.0,
                uom="PCS",
                rate=111.99,
                discount=0.0,
                amount=671.94,
                mapped_name="DM PASANTA PENNE BOGO WW (500G*20)RS",
                mapped_unit="PCS",
                is_mapped=True
            )
        ]
    )
    # Mock queue_operation to just print XML instead of db insert
    import backend.routers.purchase_item
    backend.routers.purchase_item.queue_operation = lambda action, xml, *args: print(xml)
    await post_purchase_item(payload)

if __name__ == "__main__":
    asyncio.run(main())
