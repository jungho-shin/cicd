export default function App({ env, version }) {
  return (
    <main>
      <h1>react-app</h1>
      <dl>
        <dt>environment</dt>
        <dd>{env}</dd>
        <dt>version</dt>
        <dd>{version}</dd>
      </dl>
    </main>
  )
}
