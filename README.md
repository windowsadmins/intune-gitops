# intune-gitops

The Intune half of a GitOps-managed fleet, for **both Windows and macOS**: the
assignment engine that turns a reviewed manifest tree into Intune assignments,
the enrollment group ladder that turns inventory into Entra groups, and a set of
tenant tools.

It is the shared engine behind
[cimian-gitops](https://github.com/windowsadmins/cimian-gitops) (Windows, Cimian)
and [munki-gitops](https://github.com/rodchristiansen/munki-gitops) (macOS,
Munki).
