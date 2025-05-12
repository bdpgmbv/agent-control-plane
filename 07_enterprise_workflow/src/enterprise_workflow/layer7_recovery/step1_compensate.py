"""
LAYER 7 - UNDOING WHAT ALREADY HAPPENED
=======================================
Step six failed for good. Steps three, four and five already changed the world:
there is a directory account, an equipment order and a licence subscription for
somebody who is not joining.

A database transaction would roll those back. Nothing here can, because they did
not happen in a database - they happened at a vendor, and a vendor has no memory
of our intentions. So instead of undoing them, we do the OPPOSITE of each, in
reverse order, and record that we did.

REVERSE ORDER IS NOT TIDINESS
-----------------------------
The licences are attached to the account. Delete the account first and the
licence release fails, because the thing it was attached to is gone - and now
the subscription bills forever with nothing pointing at it. Later steps depend
on earlier ones, so undoing has to walk back the way it came.

COMPENSATION IS NOT A ROLLBACK
------------------------------
It leaves the world in an ACCEPTABLE state, not the original one. The account
existed for four minutes; that is in the vendor's audit log and will be there
for seven years. If a welcome email had gone out, nothing here would unsend it -
which is why `notify_manager` is the last step in the workflow. Ordering steps
by how hard they are to undo is a design decision made when the workflow is
written, and it is the only defence against a step that cannot be undone at all.

WHEN COMPENSATION ITSELF FAILS
------------------------------
It is asking the same flaky vendors that caused the problem. A failed
compensation is not retried into the ground and is not swallowed: the run is
left in COMPENSATING, the failure is recorded against the effect, and a person
is told which specific thing is still outstanding. An orphaned licence
subscription somebody knows about costs money; one nobody knows about costs
money for years.
"""

from dataclasses import dataclass, field

from enterprise_workflow.layer2_models.schemas import RunState
from enterprise_workflow.layer5_steps.step1_base import StepContext


@dataclass
class CompensationReport:
    run_id: str
    undone: list[str] = field(default_factory=list)
    could_not_undo: list[str] = field(default_factory=list)
    nothing_to_undo: list[str] = field(default_factory=list)
    finished: bool = False

    def summary(self) -> str:
        parts = []
        if len(self.undone) > 0:
            parts.append("undid %d" % len(self.undone))
        if len(self.nothing_to_undo) > 0:
            parts.append("%d could not be undone by design" % len(self.nothing_to_undo))
        if len(self.could_not_undo) > 0:
            parts.append("%d FAILED to undo" % len(self.could_not_undo))
        if len(parts) == 0:
            return "there was nothing to undo"
        return ", ".join(parts)


class Compensator:
    def __init__(self, store, registry, client=None, settings=None) -> None:
        self.store = store
        self.registry = registry
        self.client = client
        self.settings = settings

    def compensate_run(self, run_id: str) -> CompensationReport:
        report = CompensationReport(run_id=run_id)

        run = self.store.get_run(run_id)
        if run is None:
            return report

        # Remember why this run ended. CANCELLED and FAILED are different facts
        # and a reader needs both: "it was cancelled" and "we cleaned up after
        # it" are not the same sentence. Overwriting CANCELLED with COMPENSATED
        # made every cancelled run look like a failed one afterwards.
        ended_as = run.state

        self.store.set_run_state(run_id, RunState.COMPENSATING, error=run.error)
        self.store.add_event(run_id, "run.compensating", detail={"reason": run.error})

        context = StepContext(
            run_id=run_id, workflow_input=run.input, context=run.context,
            store=self.store, client=self.client, settings=self.settings)

        effects = self.store.list_side_effects(run_id)
        effects.reverse()   # newest first; see the note above about ordering

        for effect in effects:
            if effect.compensated:
                continue

            step = self.registry.get(effect.step_name)
            if step is None:
                report.could_not_undo.append(
                    "%s: there is no step called %r to undo it"
                    % (effect.idempotency_key, effect.step_name))
                continue

            if not step.can_compensate():
                # Recorded, not hidden. Somebody has to know an email went out.
                report.nothing_to_undo.append(
                    "%s (%s cannot be undone)" % (effect.idempotency_key, effect.step_name))
                self.store.add_event(run_id, "compensation.impossible", effect.step_name,
                                     {"effect": effect.idempotency_key})
                continue

            try:
                description = step.compensate(context, effect.detail)
            except Exception as error:
                message = "%s: %s" % (type(error).__name__, error)
                report.could_not_undo.append("%s: %s" % (effect.idempotency_key, message))
                self.store.add_event(run_id, "compensation.failed", effect.step_name,
                                     {"effect": effect.idempotency_key, "error": message})
                continue

            self.store.mark_compensated(effect.effect_id)
            report.undone.append("%s: %s" % (effect.idempotency_key, description))
            self.store.add_event(run_id, "compensation.done", effect.step_name,
                                 {"effect": effect.idempotency_key, "did": description})

        if len(report.could_not_undo) == 0:
            # A cancelled run stays cancelled. Only a FAILED one becomes
            # COMPENSATED, because there the cleanup IS the rest of the story.
            final_state = (RunState.CANCELLED if ended_as == RunState.CANCELLED
                           else RunState.COMPENSATED)
            self.store.set_run_state(run_id, final_state, error=run.error)
            self.store.add_event(run_id, "run.compensated",
                                 detail={"summary": report.summary(),
                                         "ended_as": final_state.value})
            report.finished = True
        else:
            # Left in COMPENSATING on purpose. This run is not finished; it is
            # waiting for a person, and a state of "compensated" would say the
            # opposite.
            self.store.add_event(run_id, "run.compensation_incomplete",
                                 detail={"outstanding": report.could_not_undo})

        return report
