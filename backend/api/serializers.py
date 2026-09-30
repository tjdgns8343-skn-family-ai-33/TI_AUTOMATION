from rest_framework import serializers

from .models import Review, Run


class RunCreateSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(max_length=200, required=False, allow_blank=True, default="고객")
    subject = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    body = serializers.CharField(min_length=5, max_length=8000)

    class Meta:
        model = Run
        fields = ["customer", "subject", "body"]


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["action", "comment", "created_at"]


class RunListSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = [
            "id", "customer", "subject", "status", "mode", "ticket_id", "category",
            "input_tokens", "output_tokens", "created_at", "updated_at",
        ]  # fmt: skip

    def get_category(self, run: Run) -> str:
        return (run.state.get("intake") or {}).get("category", "")


class RunDetailSerializer(RunListSerializer):
    reviews = ReviewSerializer(many=True, read_only=True)

    class Meta(RunListSerializer.Meta):
        fields = [*RunListSerializer.Meta.fields, "body", "state", "pending_review", "reviews", "error", "finished_at"]


class ReviewRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=Review.Action.choices)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["action"] == Review.Action.REVISE and not attrs["comment"].strip():
            raise serializers.ValidationError({"comment": "수정 요청에는 수정할 내용을 적어야 합니다."})
        return attrs
