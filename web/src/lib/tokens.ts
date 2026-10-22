/** D62: when an API token expires, as the lists show it — null never does. */
export function expiresText(expires: number | null) {
  return expires == null ? "永不过期" : `${new Date(expires * 1000).toLocaleDateString()} 到期`;
}
