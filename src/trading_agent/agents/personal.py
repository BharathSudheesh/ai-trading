"""Personal AI Agent: decides whether a market signal is relevant to this particular user."""

from __future__ import annotations

from ..schemas import Action, Goal, MarketSignal, PersonalContext, SignalLabel, UserProfile


class PersonalAgent:
    """Applies the user's asset list, confidence threshold and goals to a raw signal.

    Goal adjustments to the confidence the user needs before acting:
    * ``capital_preservation`` adds 3 points to the bar for BUY signals.
    * ``speculation`` lowers the bar by 2 points (never below 50%).
    """

    def evaluate(
        self,
        profile: UserProfile,
        signal: MarketSignal,
        universe: list[str],
        held: set[str],
    ) -> PersonalContext:
        sym = signal.symbol
        watch = profile.preferred_assets or universe
        if sym not in watch:
            return PersonalContext(
                relevant=False, candidate=None,
                reasons=[f"{sym} is not one of your preferred assets."],
            )

        if signal.label == SignalLabel.neutral:
            return PersonalContext(
                relevant=True, candidate=None,
                reasons=["The model sees no clear edge in either direction right now."],
            )

        action = Action.BUY if signal.label == SignalLabel.potential_buy else Action.SELL
        threshold = float(profile.min_confidence_pct or 50)
        notes: list[str] = []
        if action == Action.BUY:
            if Goal.capital_preservation in profile.goals:
                threshold += 3
                notes.append("Capital preservation is a goal, so buys need 3 points more confidence.")
            if Goal.speculation in profile.goals:
                threshold = max(50.0, threshold - 2)
                notes.append("Speculation is a goal, so the confidence bar is 2 points lower.")

        if signal.confidence * 100 < threshold:
            return PersonalContext(
                relevant=True, candidate=None,
                reasons=[
                    f"Confidence is {signal.confidence:.0%}, below the {threshold:.0f}% you require.",
                    *notes,
                ],
            )

        if action == Action.SELL and sym not in held:
            return PersonalContext(
                relevant=True, candidate=None,
                reasons=[f"The model leans down on {sym}, but you hold no shares to sell."],
            )

        notes.insert(0, f"Confidence of {signal.confidence:.0%} clears your {threshold:.0f}% bar.")
        return PersonalContext(relevant=True, candidate=action, reasons=notes)
