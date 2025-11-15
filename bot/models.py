from django.db import models

class Bounty(models.Model):
    issue_number = models.PositiveIntegerField(unique=True)
    amount = models.BigIntegerField(help_text="sats")
    payment_hash = models.CharField(max_length=64)
    paid = models.BooleanField(default=False)
    contributor = models.CharField(max_length=100, blank=True)
    lnurl = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"#{self.issue_number} – {self.amount} sats"