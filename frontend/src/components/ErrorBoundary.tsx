import { Component, ReactNode } from "react";

/** Une erreur d'affichage montre un message et un bouton, au lieu d'un écran vide. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="app" style={{ display: "grid", placeItems: "center" }}>
        <div className="card card-pad" style={{ maxWidth: 480 }}>
          <div className="modal-title">Un problème d'affichage est survenu</div>
          <p className="muted" style={{ marginTop: 6 }}>
            Tes projets ne sont pas touchés. Recharge la fenêtre ; si ça recommence, envoie le Journal.
          </p>
          <p className="faint mono" style={{ marginTop: 8, fontSize: 11 }}>{String(this.state.error.message || this.state.error)}</p>
          <button className="btn btn-primary" style={{ marginTop: 18 }} onClick={() => location.reload()}>
            Recharger
          </button>
        </div>
      </div>
    );
  }
}
