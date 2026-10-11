"""An indirect speech instruction prescribes its content as the stop words; invented policies, no model, bank or platform I/O."""
import copy
import json
import unittest

from builder.build import make_policy_prompt
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.policy import execution_rule_guidance, validate_policy
from test_goal_requirements import compiled, rehash
from test_uncertain_stop_line import custom, packed, spoken, stated


WORDS = "the request could not be confirmed"
REFUSED = "uncertain_stop_explanation must appear literally"


def errors(words, sentence):
    return " ".join(validate_policy(stated(words, sentence)[0], strict=True))


class SourcedStopWordsTests(unittest.TestCase):
    def test_shape_and_prompt_prescribe_the_content_of_an_indirect_instruction(self):
        shape = execution_rule_guidance()["uncertain_stop_explanation"]
        self.assertIn("or true only when the source requires an explanation there without stating its content", shape)
        self.assertIn('An indirect instruction such as "explain/say/tell (the caller) (only) that X" states the content and '
                      'prescribes X as the words: the value is X exactly as written, without the instruction verb, "only" or "that"', shape)
        header, data = make_policy_prompt(*packed()[1:]).split("\n\nAUTHORIZED INPUTS:\n", 1)
        self.assertIn('so encode it in uncertain_stop_explanation. An indirect instruction such as "explain/say/tell (the caller) '
                      '(only) that X" prescribes X as the words to say: quote X exactly as written; true is only for a source '
                      'that requires an explanation there without stating its content.', " ".join(header.split()))
        self.assertEqual(json.loads(data)["execution_interface"]["rule_shapes"]["uncertain_stop_explanation"], shape)

    def test_the_content_of_each_indirect_form_is_spoken_verbatim(self):
        for instruction in ("Explain only that", "Say that", "Tell the caller that", "Tell the caller only that"):
            with self.subTest(instruction=instruction):
                sentence = f"{instruction} {WORDS}; do not say it failed."
                self.assertNotIn(REFUSED, errors(WORDS, sentence))
                _, _, workflow = compiled(*stated(WORDS, sentence))
                self.assertEqual(spoken(workflow), custom(WORDS))

    def test_partial_and_negated_fragments_are_still_refused(self):
        sentence = f"Explain only that {WORDS}; do not explain that it failed, and never tell the caller that it succeeded."
        self.assertNotIn(REFUSED, errors(WORDS, sentence))
        for words in ("he request could not be confirmed", "the request could not be confirm", "it failed", "it succeeded"):
            with self.subTest(words=words):
                self.assertIn(REFUSED, errors(words, sentence))
        # The sealed compiler applies the same check, including the negated explain.
        _, context, _ = compiled(*packed(WORDS))
        contract = copy.deepcopy(context["contract"])
        for source in contract["active_rules"]["uncertain_stop_explanation"]["provenance"]:
            source["quote"] = f"Do not explain that {WORDS}."
        rehash(contract)
        with self.assertRaisesRegex(GuardCompileError, "must occur literally as whole words in its own sourced quotation"):
            compile_guarded_workflow(**dict(context, contract=contract))


if __name__ == "__main__":
    unittest.main()
