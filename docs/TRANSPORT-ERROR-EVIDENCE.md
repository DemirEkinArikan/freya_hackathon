# Qualified errors without JSON bodies

Some supplied API contracts guarantee nonpersistence for an exact HTTP error but
do not require a JSON response body. The guard now preserves the actual envelope
status when its body is missing, text, a list, or an object. A non-object body
becomes a metadata-only mapping; this does not manufacture a success flag or a
record identity. A body-provided transport-status field is removed before the
actual envelope status is projected.

Promise replay requires an exact frozen nonpersistence matcher. Its error-literal
condition, when supplied, must still match. A successful HTTP response requires
an explicit negative success flag. A qualified non-success HTTP response may
omit that flag. Malformed flags, invalid or missing transport status, affirmative
success on an error, and a conflicting record identity enter quarantine without
replay. The uncertain request and previously recorded promises remain intact.

Qualified failures consume the supplied failure counter, retry the same confirmed
request, and apply only the supplied disposition at exhaustion. This adds no
default status, reason, operation permission, persistence guarantee, or automatic
transport retry.

`tests/test_guard_transport_errors.py` executes the exact compiled post-hook,
response mapping and result code with independently authored envelopes, including
renamed request/result fields and opaque codes. These are local runtime checks;
they do not establish live platform transport behavior or conversation compliance.
