import { useState } from "react";
import { Button, Modal } from "../../components/ui";

interface Ask {
  title: string;
  body: string;
  ok: string;
  run: () => void;
}

/** Confirmation avant de retirer une proposition (zone de l'IA, passage). « Ne plus demander » vaut pour ce projet,
 *  sur ce PC. Entrée confirme, Échap annule. */
export function useConfirm(pid: string) {
  const key = `se7encensored.noConfirm.${pid}`;
  const [ask, setAsk] = useState<Ask | null>(null);
  const [skip, setSkip] = useState(false);
  const silent = () => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  };
  const confirm = (a: Ask) => {
    if (silent()) a.run();
    else {
      setSkip(false);
      setAsk(a);
    }
  };
  const accept = () => {
    if (!ask) return;
    if (skip) {
      try {
        localStorage.setItem(key, "1");
      } catch {
        /* ignore */
      }
    }
    const run = ask.run;
    setAsk(null);
    run();
  };
  const modal = (
    <Modal
      open={!!ask}
      onClose={() => setAsk(null)}
      eyebrow="Confirmer"
      title={ask?.title}
      actions={
        <>
          <label className="row faint cz-noask">
            <input type="checkbox" checked={skip} onChange={(e) => setSkip(e.target.checked)} /> Ne plus demander pour ce projet
          </label>
          <span className="spacer" />
          <Button variant="ghost" onClick={() => setAsk(null)}>Annuler</Button>
          <Button variant="primary" onClick={accept} autoFocus>{ask?.ok ?? "Retirer"}</Button>
        </>
      }
    >
      {ask?.body}
    </Modal>
  );
  return { confirm, modal, open: !!ask, accept };
}
