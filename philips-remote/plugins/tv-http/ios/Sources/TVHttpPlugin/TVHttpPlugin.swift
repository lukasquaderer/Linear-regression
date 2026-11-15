import Foundation
import Capacitor

/// Plain HTTP(S) requests to the TV, made natively so they are not subject to
/// the web view's CORS and mixed-content rules.
@objc(TVHttpPlugin)
public class TVHttpPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "TVHttpPlugin"
    public let jsName = "TVHttp"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "request", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "localAddress", returnType: CAPPluginReturnPromise)
    ]

    /// The phone's IPv4 address on Wi-Fi, used to work out which network to search for the TV.
    @objc func localAddress(_ call: CAPPluginCall) {
        var address = ""
        var interfaces: UnsafeMutablePointer<ifaddrs>?
        if getifaddrs(&interfaces) == 0 {
            var pointer = interfaces
            while let current = pointer {
                let interface = current.pointee
                if let socketAddress = interface.ifa_addr,
                   socketAddress.pointee.sa_family == UInt8(AF_INET),
                   String(cString: interface.ifa_name) == "en0" {
                    var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                    if getnameinfo(socketAddress, socklen_t(socketAddress.pointee.sa_len),
                                   &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 {
                        address = String(cString: host)
                    }
                }
                pointer = interface.ifa_next
            }
            freeifaddrs(interfaces)
        }
        call.resolve(["address": address])
    }

    @objc func request(_ call: CAPPluginCall) {
        guard let urlString = call.getString("url"), let url = URL(string: urlString) else {
            call.reject("Invalid URL")
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = call.getString("method") ?? "GET"
        request.timeoutInterval = call.getDouble("timeout") ?? 4
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let body = call.getString("body") {
            request.httpBody = body.data(using: .utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let delegate = TVSessionDelegate(username: call.getString("username"),
                                         password: call.getString("password"))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCredentialStorage = nil
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)

        session.dataTask(with: request) { data, response, error in
            session.finishTasksAndInvalidate()
            if let error = error {
                call.reject(error.localizedDescription)
                return
            }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let text = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            call.resolve(["status": status, "data": text])
        }.resume()
    }
}

final class TVSessionDelegate: NSObject, URLSessionTaskDelegate {
    private let username: String?
    private let password: String?

    init(username: String?, password: String?) {
        self.username = username
        self.password = password
    }

    private func handle(_ challenge: URLAuthenticationChallenge,
                        _ completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let space = challenge.protectionSpace
        switch space.authenticationMethod {
        case NSURLAuthenticationMethodServerTrust:
            // Philips TVs present a self-signed certificate, so it cannot be verified.
            if let trust = space.serverTrust {
                completionHandler(.useCredential, URLCredential(trust: trust))
            } else {
                completionHandler(.performDefaultHandling, nil)
            }
        case NSURLAuthenticationMethodHTTPDigest:
            if challenge.previousFailureCount == 0, let username = username, let password = password {
                completionHandler(.useCredential,
                                  URLCredential(user: username, password: password, persistence: .none))
            } else {
                // No (working) credentials: let the 401 response through to the caller.
                completionHandler(.performDefaultHandling, nil)
            }
        default:
            completionHandler(.performDefaultHandling, nil)
        }
    }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        handle(challenge, completionHandler)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        handle(challenge, completionHandler)
    }
}
