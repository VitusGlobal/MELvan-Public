# Health-Domain Safeguards

Status: product requirements for MELvan instances used in health or mental-health programs. These safeguards constrain MELvan's program-evaluation role; they do not make MELvan a clinical system.

## Purpose boundary

MELvan captures and reviews evidence about program implementation and effectiveness. It may process Statements that describe clinical services, research, health outcomes, or participant experiences, but it must not:

- Diagnose or screen an individual.
- Estimate an individual's clinical risk or prognosis.
- Recommend, select, alter, or evaluate treatment for an individual.
- Predict an individual's treatment response.
- Infer a biomarker, disorder, symptom, or health state from text, voice, images, behaviour, or device data.
- Present program Evidence as proof of clinical efficacy or safety.

Those actions are outside MELvan's intended use cases. MELvan may still classify and map an underlying program-relevant Statement when it can do so without making a prohibited clinical inference. A dedicated request detector and refusal response remain planned safeguards rather than a capability of the current implementation.

## Program Evidence versus clinical evidence

In MELvan, **Evidence** means a Reviewer-approved Statement analysis relevant to program monitoring, learning, or evaluation. It is not a claim that a clinical intervention is effective, safe, or appropriate for an individual or population.

MELvan must preserve evidentiary qualifiers and attribution. For example, a participant report, staff observation, meeting note, service count, and validated outcome measure are different source types and must not be rewritten as if they have equal evidentiary strength. Summaries must not convert correlation into causation, anecdote into validated effect, or a program observation into a clinical conclusion.

## Sensitive-data controls

Patient, genomic, biometric, clinical-history, behavioural, and device-derived data are denied by default. A program may process a specifically approved category only when it has documented:

- A lawful and purpose-limited basis for collection and use.
- Appropriate consent or other applicable authority.
- Data minimization and de-identification requirements.
- Role-based access, storage, sharing, retention, and deletion rules.
- Whether the data may be sent to a model provider and under what controls.
- A prohibition on unapproved secondary use or model training.

Reporter convenience does not override these controls. A camera, microphone, upload, or transcription feature must apply the same policy to original media and derived content.

## Contextual quality and fairness

Aggregate quality measures can conceal systematic failures. Subject to privacy and legal review, MELvan evaluation should stratify classification agreement, mapping agreement, correction effort, low-confidence rates, and failure rates across relevant contexts such as:

- Program area and framework element.
- Input modality and source type.
- Language or dialect.
- Reporter group and deployment setting.
- Other approved characteristics needed to test equitable performance.

MELvan must not collect sensitive attributes solely because they might be useful for analysis. The evaluation plan must define why each dimension is necessary, how small groups are protected, and what disparity requires investigation or remediation.

## Real-world monitoring

Model and workflow performance must be monitored after deployment, not inferred only from development tests. Each supported deployment should track applicable measures such as:

- Capture, upload, extraction, and processing failures.
- Media rejection and transcription failure rates.
- Latency, retries, abandonment, and duplicate creation.
- Low-confidence, no-suitable-match, and Reviewer-correction rates.
- Performance changes after model, prompt, framework, corpus, device, connectivity, or workflow changes.

Material deterioration or disparity triggers investigation, rollback or escalation according to an approved release policy. Individual Reviewer feedback remains record-level truth; aggregate findings inform a separately governed classifier or product change.

## Delivery impact

The current implementation establishes the non-clinical product scope and preserves program-level framing in its analyses. Dedicated clinical-request refusal, contextual fairness metrics, operational drift monitoring, multimodal controls, and approved sensitive-data pathways remain planned requirements.

This specification was informed by two supplementary research references, which are not part of MELvan's all-instance foundational RBM corpus:

- Johnson et al., *Precision Medicine, AI, and the Future of Personalized Health Care* (2021), DOI `10.1111/cts.12884`.
- Fernandes et al., *The New Field of Precision Psychiatry* (2017), DOI `10.1186/s12916-017-0849-x`.
